"""Isolated ML environment. Only image, prompt and pinned settings enter here."""
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import sys
import time
from pathlib import Path


def write(path,value):
    temporary = path.with_suffix('.partial')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,sort_keys=True,allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary,path)


def main():
    # Bounds output/log files even if a provider dependency becomes noisy.
    resource.setrlimit(resource.RLIMIT_FSIZE,(1048576,1048576))
    request = json.loads(Path(sys.argv[1]).read_bytes())
    root = Path(sys.argv[2])
    profile = request['profile']
    progress = {'python':platform.python_version(),'machine':platform.machine(),'device':'cpu','dependencies':{},'timings_ms':{}}
    for name in ['torch','torchvision','transformers','tokenizers','huggingface-hub','pillow','numpy','accelerate','safetensors']:
        try:
            progress['dependencies'][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            progress['dependencies'][name] = None
    write(root/'progress.json',progress)
    try:
        from robotics_data_platform.planning.model_cache import verified_snapshot
        snapshot,cache_manifest = verified_snapshot(Path('/models'),profile)
        progress['model_files_manifest_sha256'] = cache_manifest
        write(root/'progress.json',progress)
        import torch
        from PIL import Image
        from transformers import AutoModelForVision2Seq,AutoProcessor
        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        torch.manual_seed(7)
        started = time.perf_counter()
        processor = AutoProcessor.from_pretrained(snapshot,local_files_only=True,trust_remote_code=False)
        processor.image_processor.size = {'longest_edge':512}
        processor.image_processor.do_image_splitting = False
        model = AutoModelForVision2Seq.from_pretrained(snapshot,local_files_only=True,trust_remote_code=False,torch_dtype=torch.float32,_attn_implementation='eager').eval()
        progress['timings_ms']['load'] = (time.perf_counter()-started)*1000
        write(root/'progress.json',progress)
        started = time.perf_counter()
        with Image.open(request['image_path']) as source:
            image = source.convert('RGB')
        messages = [{'role':'user','content':[{'type':'image'},{'type':'text','text':request['prompt']}]}]
        rendered_prompt = processor.apply_chat_template(messages,add_generation_prompt=True)
        inputs = processor(text=rendered_prompt,images=[image],return_tensors='pt')
        if inputs['input_ids'].shape[1] > 4096:
            raise ValueError('Provider input token limit exceeded')
        progress['input_tokens'] = int(inputs['input_ids'].shape[1])
        progress['rendered_prompt_sha256'] = hashlib.sha256(rendered_prompt.encode()).hexdigest()
        write(root/'rendered-prompt.json',{'text':rendered_prompt})
        progress['timings_ms']['preprocess'] = (time.perf_counter()-started)*1000
        write(root/'progress.json',progress)
        started = time.perf_counter()
        with torch.inference_mode():
            generated = model.generate(**inputs,max_new_tokens=80,do_sample=False)
        new_tokens = generated[:,inputs['input_ids'].shape[1]:]
        response = processor.batch_decode(new_tokens,skip_special_tokens=True)[0].encode('utf-8')
        progress['timings_ms']['generation'] = (time.perf_counter()-started)*1000
        progress['new_tokens'] = int(new_tokens.shape[1])
        progress['token_cap_reached'] = progress['new_tokens'] == 80
        progress['process_peak_rss_mib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
        write(root/'generated-tokens.json',{'token_ids':new_tokens[0].tolist()})
        (root/'response.bin').write_bytes(response)
        write(root/'progress.json',progress)
    except Exception as error:
        progress['error_type'] = type(error).__name__
        write(root/'progress.json',progress)
        raise


if __name__ == '__main__':
    main()

param([Parameter(Mandatory=$true)][string]$OutputRoot)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path -Parent $PSScriptRoot
$project='rdp-step11-repro-'+[guid]::NewGuid().ToString('N').Substring(0,8)
$priorPort=$env:API_PORT
$listener=[System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,0)
$listener.Start()
$port=$listener.LocalEndpoint.Port
$listener.Stop()
$env:API_PORT=[string]$port
$additionalPorts=@()
foreach($purpose in @('prefect','dashboard')){
    do {$candidateListener=[System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,0);$candidateListener.Start();$candidate=$candidateListener.LocalEndpoint.Port;$candidateListener.Stop()}while($candidate -eq $port -or $additionalPorts -contains $candidate)
    $additionalPorts+=@($candidate)
}
$prefectPort=$additionalPorts[0]
$dashboardPort=$additionalPorts[1]
$output=[System.IO.Path]::GetFullPath($OutputRoot)
New-Item -ItemType Directory -Path $output -Force | Out-Null
$fixtures=Join-Path $output 'repro-fixtures'
New-Item -ItemType Directory -Path $fixtures -Force | Out-Null
$override=Join-Path $fixtures 'compose.repro.yaml'
@"
services:
  prefect:
    ports: !override
      - "127.0.0.1:${prefectPort}:4200"
    environment:
      PREFECT_UI_API_URL: "http://127.0.0.1:${prefectPort}/api"
  dashboard:
    ports: !override
      - "127.0.0.1:${dashboardPort}:8501"
"@ | Set-Content -LiteralPath $override -Encoding utf8
function Invoke-RdpCompose {
    param([string[]]$Arguments)
    $value=& docker compose --project-name $project --file "$repoRoot/compose.yaml" --file $override @Arguments
    if($LASTEXITCODE -ne 0){throw "Isolated reproduction command failed: $($Arguments -join ' ')"}
    return $value
}
function As-JsonObject {param($Lines) return (($Lines -join "`n") | ConvertFrom-Json)}
Push-Location $repoRoot
try {
    Invoke-RdpCompose -Arguments @('config','--quiet') | Out-Null
    Invoke-RdpCompose -Arguments @('up','-d','--wait','api') | Out-Null
    $ready=Invoke-RestMethod "http://localhost:$port/readyz"
    if($ready.status -ne 'ready'){throw 'Fresh API is not ready'}
    $robot=As-JsonObject (Invoke-RdpCompose -Arguments @('--profile','simulation','run','--rm','simulator','run','--object-id','package_01','--destination-id','zone_a','--seed','101'))
    $intake=As-JsonObject (Invoke-RdpCompose -Arguments @('--profile','ingestion','run','--rm','ingestor'))
    if($intake.processed -ne 1 -or $intake.results[0].status -ne 'INGESTED'){throw 'Fresh baseline intake failed'}
    $events=Invoke-RestMethod "http://localhost:$port/api/v1/episodes/$($robot.episode_id)/events"
    $observation=$events.items | Where-Object event_type -eq 'observation.captured' | Select-Object -First 1
    $model=As-JsonObject (Invoke-RdpCompose -Arguments @('--profile','vlm','run','--no-deps','--rm','-v','robotics-data-platform_model_cache:/models:ro','planner','run','--source-episode-id',$robot.episode_id,'--observation-id',$observation.payload.observation_id,'--instruction','Move the red package to zone A.'))
    if($model.provider_status -ne 'COMPLETED'){throw 'Fresh offline inference did not complete'}
    $proposalIntake=As-JsonObject (Invoke-RdpCompose -Arguments @('--profile','proposal-ingestion','run','--rm','proposal-ingestor'))
    if($proposalIntake[0].status -ne 'INGESTED'){throw 'Fresh proposal intake failed'}
    $case=@{case_id='repro-red';request=@{object_id='package_01';destination_id='zone_a';seed=101};expected_outcome='SUCCEEDED'}
    @{cases=@($case)} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$fixtures/definition.json" -Encoding utf8
    @{results=@(@{case_id='repro-red';request=$case.request;expected_outcome='SUCCEEDED';actual=@{episode_id=$robot.episode_id}})} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$fixtures/assignment.json" -Encoding utf8
    $freeze=As-JsonObject (Invoke-RdpCompose -Arguments @('--profile','pipelines','run','--no-deps','--rm','-v',"${fixtures}:/fixtures:ro",'pipeline','python','-m','robotics_data_platform.analytics.cli','freeze','--definition','/fixtures/definition.json','--assignment','/fixtures/assignment.json'))
    Invoke-RdpCompose -Arguments @('--profile','pipelines','up','-d','--wait','prefect') | Out-Null
    $dataset=As-JsonObject (Invoke-RdpCompose -Arguments @('--profile','pipelines','run','--rm','-e','RDP_DATASET_DEFINITION_PATH=/fixtures/definition.json','-e','RDP_CAPTURE_ASSIGNMENT_PATH=/fixtures/assignment.json','-v',"${fixtures}:/fixtures:ro",'pipeline'))
    if($dataset.input_sha256 -ne $freeze.input_sha256){throw 'Prefect used different frozen inputs'}
    $rebuild=As-JsonObject (Invoke-RdpCompose -Arguments @('--profile','pipelines','run','--no-deps','--rm','pipeline','python','-m','robotics_data_platform.analytics.cli','rebuild','--identity',$dataset.build_fingerprint))
    if(-not $rebuild.metrics_match){throw 'Fresh dataset rebuild mismatch'}
    $task=@{task_set_version='1.0.0';name='clean-workspace-smoke-v1';split='development_retrospective';disclosure='Isolated clean-volume reproduction, not fixed benchmark performance.';cases=@(@{case_id='red';category='supported';instruction='Move the red package to zone A.';expected=@{status='ready';object_id='package_01';destination_id='zone_a'};baseline_request=@{object_id='package_01';destination_id='zone_a'};baseline_outcome='SUCCEEDED';seeds=@(101)})}
    $task | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath "$fixtures/task-set.json" -Encoding utf8
    @{assignment_version='1.0.0';bindings=@(@{case_id='red';seed=101;mode='BASELINE_STRUCTURED';attempt_id=$robot.episode_id},@{case_id='red';seed=101;mode='VLM_EVALUATION';attempt_id=$model.proposal_id})} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$fixtures/evaluation-assignment.json" -Encoding utf8
    $evaluation=As-JsonObject (Invoke-RdpCompose -Arguments @('--profile','evaluation','run','--rm','-v',"${fixtures}:/fixtures:ro",'evaluator','python','-m','robotics_data_platform.evaluation.reports','--dataset-id',$dataset.dataset_id,'--task-set','/fixtures/task-set.json','--assignments','/fixtures/evaluation-assignment.json'))
    $verified=Invoke-RestMethod "http://localhost:$port/api/v1/evaluations/$($evaluation.report_id)"
    Invoke-RdpCompose -Arguments @('--profile','dashboard','up','-d','--wait','dashboard') | Out-Null
    $dashboardHealth=Invoke-WebRequest "http://localhost:$dashboardPort/_stcore/health"
    $prefectHealth=Invoke-WebRequest "http://localhost:$prefectPort/api/health"
    if($dashboardHealth.StatusCode -ne 200 -or $prefectHealth.StatusCode -ne 200){throw 'Fresh dashboard/orchestrator health failed'}
    $baselineBefore=Invoke-RestMethod "http://localhost:$port/api/v1/ingestion/counts"
    $proposalBefore=Invoke-RestMethod "http://localhost:$port/api/v1/proposal-ingestion/counts"
    $again=As-JsonObject (Invoke-RdpCompose -Arguments @('--profile','ingestion','run','--rm','ingestor','python','-m','robotics_data_platform.ingestion.cli','replay'))
    $againProposal=As-JsonObject (Invoke-RdpCompose -Arguments @('--profile','proposal-ingestion','run','--rm','proposal-ingestor','python','-m','robotics_data_platform.planning.ingestion','replay'))
    if($again.processed -ne 1 -or $again.results[0].status -ne 'DUPLICATE' -or $againProposal[0].status -ne 'DUPLICATE'){throw 'Fresh raw replay was not idempotent'}
    $baselineAfter=Invoke-RestMethod "http://localhost:$port/api/v1/ingestion/counts"
    $proposalAfter=Invoke-RestMethod "http://localhost:$port/api/v1/proposal-ingestion/counts"
    if(($baselineBefore | ConvertTo-Json -Compress) -ne ($baselineAfter | ConvertTo-Json -Compress) -or ($proposalBefore | ConvertTo-Json -Compress) -ne ($proposalAfter | ConvertTo-Json -Compress)){throw 'Fresh replay changed counts'}
    @{project=$project;api_port=$port;prefect_port=$prefectPort;dashboard_port=$dashboardPort;dashboard_health=[int]$dashboardHealth.StatusCode;prefect_health=[int]$prefectHealth.StatusCode;ready=$ready;baseline_episode_id=$robot.episode_id;proposal_id=$model.proposal_id;provider_status=$model.provider_status;schema_valid=$model.validation.schema_valid;dataset_id=$dataset.dataset_id;rebuild=$rebuild;evaluation=$verified;baseline_counts=$baselineAfter;proposal_counts=$proposalAfter;baseline_replay=$again;proposal_replay=$againProposal;limits='Fresh PostgreSQL/capture/raw/proposal/dataset/evaluation/Prefect volumes and network, real local Prefect flow and dashboard. Pinned built images and prepared model weights were reused; weights were mounted read-only. No cold internet download or other host platform tested.'} | ConvertTo-Json -Depth 25 | Set-Content -LiteralPath (Join-Path $output 'clean-workspace.json') -Encoding utf8
    Write-Output "Clean-volume reproduction completed in isolated project $project"
} finally {
    try {Invoke-RdpCompose -Arguments @('--profile','*','down') | Out-Null} finally {$env:API_PORT=$priorPort;Pop-Location}
}

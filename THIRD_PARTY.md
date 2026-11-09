# Third-party simulator asset

The simulator build fetches the Franka Emika Panda description from Google DeepMind's MuJoCo Menagerie at commit `98a70c569c4c1b6da467a20012f766ee1f8dc521`:

https://github.com/google-deepmind/mujoco_menagerie/tree/98a70c569c4c1b6da467a20012f766ee1f8dc521/franka_emika_panda

That asset directory carries Apache License 2.0. Its original license and README are preserved in the image at `/opt/robot-assets/franka_emika_panda/`. The repository's MIT license applies to its original application code, not a relicensing of the asset or dependencies.

The application derives a scene in memory by adding camera/light/plane/package/zone definitions and a grasp site, selecting simulation settings, and removing the asset's home keyframe. Original mesh assets are unmodified. The resulting scene description, pinned asset revision, original Panda XML hash and application/controller source hashes are recorded with each executed episode.

Python, MuJoCo, NumPy, Pillow and other dependencies retain their respective licenses. VLM weights are not embedded in this simulator image.

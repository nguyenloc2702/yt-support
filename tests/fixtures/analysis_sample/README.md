# Analysis Sample Fixtures

Fixtures for block-based workflow testing.

## Files

- `sample_10s.mp4` - 10-second test video (1920x1080, 30fps)
- `sample_10s.wav` - Extracted audio from sample_10s.mp4
- `transcript_sample.json` - Expected ASR output
- `analysis_expected.json` - Expected analysis document
- `block_001_audio.wav` - Sample audio for block 1
- `block_002_audio.wav` - Sample audio for block 2

## Usage

```python
from pathlib import Path
FIXTURE_DIR = Path("tests/fixtures/analysis_sample")
```

## Generation

To regenerate fixtures:
```bash
# Extract audio
ffmpeg -i sample_10s.mp4 -vn -acodec pcm_s16le -ar 48000 -ac 2 sample_10s.wav

# Get duration
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 sample_10s.mp4

# Get fps
ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of default=noprint_wrappers=1:nokey=1 sample_10s.mp4
```

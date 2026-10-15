# Dataset card

## UR Fall Detection Dataset (URFD)

Official source: <https://fenix.ur.edu.pl/~mkepski/ds/uf.html>

The dataset has 70 sequences: 30 simulated falls and 40 activities of daily
living. Fall sequences have two Kinect views; ADL sequences have camera 0 only.
This project defaults to camera 0 so every sequence has a common view.

Two download profiles are supported:

- `preview`: the official 640×240 MP4 composites (about 121 MB for every
  available view). The RGB image is the right 320×240 half.
- `rgb`: the full camera-0 RGB PNG archives (about 4.49 GB compressed).

The official depth-feature CSV files supply per-frame posture annotations:
`-1` not lying, `0` transition, and `1` lying. They do **not** supply an
eight-activity label and are not themselves fall-vs-ADL labels. Fall-event
targets used by this project are derived from sequence type plus sustained
posture transitions; provenance is saved with every prepared sequence.

URFD does not publish a reliable subject-to-sequence map. The project groups by
whole sequence to prevent frame leakage and describes the result as
cross-sequence evaluation.

### License and citation

URFD is licensed under
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) for
non-commercial academic use. Commercial use requires contacting the dataset
authors. The downloader requires an explicit acknowledgement flag.

Please cite:

> Bogdan Kwolek and Michal Kępski, “Human fall detection on embedded platform
> using depth maps and wireless accelerometer,” Computer Methods and Programs
> in Biomedicine, 117(3), 489–501, 2014.
> <https://doi.org/10.1016/j.cmpb.2014.09.005>

## 3D Skeletons UP-Fall (optional research subset)

Zenodo record: <https://doi.org/10.5281/zenodo.12773013>

The small five-subject archive is retained as an optional impact-detection
experiment. Its actual files contain 33 XYZ joints plus a binary impact column.
They do not contain all UP-Fall activities or the complete 850+ GB multimodal
dataset. License: CC BY 4.0.

## Ethical limitations

All public falls are acted by healthy participants in controlled settings.
Performance does not establish medical safety or real-world reliability for
older adults. The system is an assistive research prototype, not a replacement
for emergency services or human supervision.

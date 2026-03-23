## AI Game QA Tester

This project explores building a system that learns from a single human playthrough of a game and then replays it with variations in keystrokes to explore new states. It uses sprite-based tracking and simple exploration heuristics.

The goal was to approximate QA-style testing by enabling automated traversal beyond the original playthrough.

### Current Implementation

* Gameplay recording (frames, keystrokes)
* Sprite tracking using OpenCV (template matching + optical flow)
* Replay system with stochastic input variation
* Basic session analysis (FPS, glitches, tracking behavior)

### Limitations

The project was not completed due to several practical challenges in generalising across games:

* Difficulty in reliably representing game state using sprite tracking
* Large state space leading to redundant or ineffective exploration
* Lack of a clear reward signal to guide exploration
* Reliance on heuristics rather than a learnable or planned policy

As a result, this remains an exploratory prototype rather than a fully developed AI system.

### Related Work

For a more modern approach using deep learning for gameplay automation, see:
https://github.com/ardamavi/Game-Bot

Extending such approaches to QA would require additional logic for detecting and logging issues.

### Test Environment

Tested on:
SpaceWalk by Angel1841
https://github.com/Angel1841/Space-Walk

### Credits

https://github.com/Angel1841/Space-Walk

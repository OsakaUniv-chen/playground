mkvirtualenv wolf --python=python3.10
pip3 install -r requirements_train.txt
pip3 install pyaudio pyudev bluepy pykeigan-motor
pip3 install rosbags
pip3 install mediapipe -c constraints.txt

# 2026-07-08: utils/head_box.py + head_orientation.py need the legacy
# mp.solutions.face_detection/face_mesh API, which mediapipe>=0.10.20 removed.
# Downgraded to 0.10.14 (matches analysis2/code/requirements.txt) and re-pinned
# numpy to 1.26.4 (mediapipe 0.10.14's own deps, e.g. jax, otherwise drag numpy
# to 2.x) + numba to 0.59.1 (0.57.0 only allows numpy<1.25).
pip3 install "numpy==1.26.4" "mediapipe==0.10.14" "numba==0.59.1"

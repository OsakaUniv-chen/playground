#!/bin/bash

SESSION="boxie_ros"

# 1. check if the same session already exist
tmux has-session -t $SESSION 2>/dev/null
if [ $? -eq 0 ]; then
    echo "Session $SESSION already exists. Attaching..."
    tmux attach -t $SESSION
    exit 0
fi

# 2. window 1: Keigan Ali
tmux new-session -d -s $SESSION -n "Rover"
tmux send-keys -t $SESSION:0 "source ~/ros2_ws/install/setup.bash" C-m
tmux send-keys -t $SESSION:0 "ros2 launch rover ali_launch.py" C-m

# 3. window 2: Keigan Motors
tmux new-window -t $SESSION -n "Keigan"
tmux send-keys -t $SESSION:1 "source ~/ros2_ws/install/setup.bash" C-m
tmux send-keys -t $SESSION:1 "ros2 run keigan_motor boxie_motor_bluetooth" C-m

# 4. default open window 1
tmux select-window -t $SESSION:1
tmux attach-session -t $SESSION

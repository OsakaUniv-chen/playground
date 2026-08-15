# Teleoperation System Architecture

Based on Liliana's `teleop_interface` + `webrtc_singalling_proc` architecture, adapted to our hardware (Xacti camera, 16ch microphone array, microphone-speaker, head yaw/pitch, Keigan ALI).

## 0. Inventory

**4 hosts / 10 devices.**

| Host | Running components | Note |
|---|---|---|
| PC-A Signage terminal | Chrome rotation script only | Not connected to the other PCs |
| PC-B Robot miniPC | **[Publish] gst**<br>・Xacti Cam (1080×1080)<br>・16ch microphone array<br>・Onboard microphone<br>**[Receive] gst**<br>・Speaker (operator microphone)<br>**[Receive] ROS**<br>・Head yaw / pitch<br>・Keigan ALI | ip1 |
| PC-C Operator terminal | OME server<br>**[Publish] ROS**<br>・Keigan ALI (gamepad)<br>**[Receive]**<br>・Operation UI screen (OME Player — onboard mic + Xacti Cam) | ip2 |
| PC-D High-performance server | Assumes a high-performance GPU for inference<br>**[Receive] gst**<br>・Xacti Cam<br>・16ch microphone array<br>・Onboard microphone / speaker (operator microphone)<br>**[Publish] ROS**<br>・Head yaw / pitch<br>・Other robot behavior decisions | ip3 |

## 1. PC-A Signage terminal

A single Linux machine displaying content in fullscreen Chrome on rotation. It cycles through Japan Meteorological Agency information (weather forecast, Himawari satellite imagery, earthquake information), switching every 10 seconds.

PC-A only displays the signage screen. It is not a touch panel, and it has no connection to the other PCs.

## 2. PC-B Robot miniPC

**Three roles.**

1. Connect the sensors and send them out over gst (Xacti, 16ch microphone array, onboard microphone)
2. Receive the operator's voice over gst and play it through the onboard speaker
3. Receive ROS2 commands and drive the base and head motors

| Resident process | Content |
|---|---|
| gst send (video) | Xacti 1080×1080 to the OME server |
| gst send (16ch) | Raw PCM from the microphone array to PC-D, the high-performance server |
| gst send (onboard mic) | 1–2ch picking up ambient sound and whether someone is speaking, to the OME server |
| gst receive (operator mic) | Operator's voice from the OME server to the onboard speaker |
| ROS receive | Keigan ALI (base), head motion (yaw / pitch), gestures (arms) |

## 3. PC-C Operator terminal

| Running component | Content |
|---|---|
| OME server | Receives the sensor streams from the robot |
| Operation UI server (ROS send) | `app.py` (Flask + SocketIO + ROS 2 publisher for Keigan ALI) `:7779` |
| Chrome | Opens `http://localhost:7779/`. Gamepad input and screen display |
| gst send (operator mic) | Operator's voice to the OME server |

## 4. PC-D High-performance server

### 4.1 Input (gst receive)

| Target | Purpose | Route |
|---|---|---|
| Xacti camera | Scene understanding | From the OME server |
| Onboard microphone | Ambient sound, whether someone is speaking | From the OME server |
| 16ch microphone array | Acoustic map generation | Direct from PC-B, the robot miniPC |
| Operator's voice | Understanding what the operator said | From the OME server |

### 4.2 Output (ROS publish)

| Target | Content |
|---|---|
| Head yaw / pitch | Deciding "who to face" |
| Other behavior decisions | Speech timing, posture, etc. |

/**
 * ゲームパッド -> 操作 UI サーバ。
 *
 * 沿用元からの変更:
 *   - 右スティック（camera）を送らない。頭部の指令元は PC-D の VLM
 *   - 速度スケーリングは送らない。PC-B の rover_driver 側で掛ける
 *     （無線を越える構成では、止める処理も機体側に無いと効かない）
 */
window.STATUS = { speed: 0, rotate: 0 };

const COMMAND_INTERVAL = 100;   // 10 Hz
const INACTIVITY_LIMIT = 3000;  // 無操作で送信停止（機体側にも watchdog あり）
const DEADZONE = 0.05;
// 割り当てのある 2 個だけ。X = 腕を下ろす / Y = 腕を上げる。
const buttonMap = { 2: "X", 3: "Y" };

let GAMEPAD_CONNECTED = false, GAMEPAD_INDEX = null;
let intervalId = null, stopTimeoutId = null, prevButtons = [];
let lastSpeedRotate = [null, null];

window.addEventListener("gamepadconnected", (e) => {
    console.info("Gamepad connected:", e.gamepad.id);
    if (!GAMEPAD_CONNECTED) {
        GAMEPAD_CONNECTED = true;
        GAMEPAD_INDEX = e.gamepad.index;
        queryGamepadLoop();
    }
});
window.addEventListener("gamepaddisconnected", (e) => {
    if (GAMEPAD_INDEX === e.gamepad.index) {
        GAMEPAD_CONNECTED = false; GAMEPAD_INDEX = null;
        stopSending();
    }
});

function applyDeadzone(v) { return Math.abs(v) < DEADZONE ? 0 : v; }

function queryGamepadLoop() {
    if (!GAMEPAD_CONNECTED) return;
    const gp = navigator.getGamepads()[GAMEPAD_INDEX];
    if (gp) {
        STATUS.speed = applyDeadzone(parseFloat((gp.axes[1] * -1).toFixed(4)));
        STATUS.rotate = applyDeadzone(parseFloat((gp.axes[0] * -1).toFixed(4)));
        updateStatusView();

        if (STATUS.speed !== lastSpeedRotate[0] || STATUS.rotate !== lastSpeedRotate[1]) {
            lastSpeedRotate = [STATUS.speed, STATUS.rotate];
            onUserInput();
        }

        gp.buttons.forEach((button, index) => {
            const was = prevButtons[index] || false;
            const now = button.pressed;
            const el = document.getElementById("btn" + (buttonMap[index] || ""));
            if (el) el.classList.toggle("active", now);

            if (now && !was) {
                // Y = 腕を上げる / X = 腕を下ろす。角度は PC-B 側で決まる。
                if (index === 3) sendCommand("arm", "up");
                else if (index === 2) sendCommand("arm", "down");
            }
            prevButtons[index] = now;
        });
    }
    requestAnimationFrame(queryGamepadLoop);
}

function updateStatusView() {
    const set = (id, v) => { const e = document.getElementById(id); if (e) e.innerText = v; };
    set("speed", STATUS.speed); set("rotate", STATUS.rotate);
}

function startSending() {
    if (intervalId) return;
    intervalId = setInterval(() => {
        sendCommand('twist', [STATUS.speed, STATUS.rotate]);
    }, COMMAND_INTERVAL);
}

function stopSending() {
    clearInterval(intervalId); intervalId = null;
    sendCommand('twist', [0, 0]);
}

function onUserInput() {
    startSending();
    clearTimeout(stopTimeoutId);
    stopTimeoutId = setTimeout(stopSending, INACTIVITY_LIMIT);
}

// ゲームパッドが無くても画面のボタンで腕を動かせるようにする
window.addEventListener("load", () => {
    const y = document.getElementById("btnY");
    const x = document.getElementById("btnX");
    if (y) y.addEventListener("click", () => sendCommand("arm", "up"));
    if (x) x.addEventListener("click", () => sendCommand("arm", "down"));
});

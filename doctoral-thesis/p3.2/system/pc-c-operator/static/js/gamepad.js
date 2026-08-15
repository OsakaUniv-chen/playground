/**
 * ゲームパッド -> 操作 UI サーバ。
 *
 * 沿用元からの変更:
 *   - 右スティック（camera）を送らない。頭部の指令元は PC-D の VLM
 *   - A ボタンを PTT（プッシュトゥトーク）に割り当て。押下/離しの
 *     両方を送るので、区間がそのまま記録に残る
 *   - 速度スケーリングは送らない。PC-B の rover_driver 側で掛ける
 *     （無線を越える構成では、止める処理も機体側に無いと効かない）
 */
window.STATUS = { speed: 0, rotate: 0, ptt: false };

const COMMAND_INTERVAL = 100;   // 10 Hz
const INACTIVITY_LIMIT = 3000;  // 無操作で送信停止（機体側にも watchdog あり）
const DEADZONE = 0.05;
const PTT_BUTTON = 0;           // A
const buttonMap = { 0: "A", 1: "B", 2: "X", 3: "Y" };

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

            if (index === PTT_BUTTON) {
                if (now !== was) {          // 押下と離しの両方を送る
                    STATUS.ptt = now;
                    sendCommand("ptt", now);
                    updateStatusView();
                }
            } else if (now && !was) {
                // Y = 腕を上げる / X = 腕を下ろす。角度は PC-B 側で決まる。
                if (index === 3) sendCommand("arm", "up");
                else if (index === 2) sendCommand("arm", "down");
                else sendCommand("button_press", index);
            }
            prevButtons[index] = now;
        });
    }
    requestAnimationFrame(queryGamepadLoop);
}

function updateStatusView() {
    const set = (id, v) => { const e = document.getElementById(id); if (e) e.innerText = v; };
    set("speed", STATUS.speed); set("rotate", STATUS.rotate);
    set("ptt", STATUS.ptt ? "ON" : "off");
    const rec = document.getElementById("recorder");
    if (rec) rec.classList.toggle("active", STATUS.ptt);
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

// マウス操作でも PTT を試せるようにしておく（ゲームパッド無しの確認用）
window.addEventListener("load", () => {
    const rec = document.getElementById("recorder");
    if (!rec) return;
    const on = () => { STATUS.ptt = true; sendCommand("ptt", true); updateStatusView(); };
    const off = () => { STATUS.ptt = false; sendCommand("ptt", false); updateStatusView(); };
    rec.addEventListener("mousedown", on);
    rec.addEventListener("mouseup", off);
    rec.addEventListener("mouseleave", off);
});

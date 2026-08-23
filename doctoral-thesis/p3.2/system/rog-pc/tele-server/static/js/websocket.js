const socket = io();
window.ws_socket = socket;

socket.on('connect', () => {
    console.info('WebSocket connected');
    sendCommand('twist', [0, 0]);
});
socket.on('disconnect', () => console.warn('WebSocket disconnected'));
socket.on('connected', (m) => {
    const el = document.getElementById('indicator');
    if (el) el.innerText = m;
});
socket.on('command_ack', (a) => { if (a.status !== 'ok') console.warn('ack', a); });

function sendCommand(type, content) { socket.emit('command', { type, content }); }
window.sendCommand = sendCommand;

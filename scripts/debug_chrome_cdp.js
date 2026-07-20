const http = require('http');
const net = require('net');
const crypto = require('crypto');
const fs = require('fs');
const { EventEmitter } = require('events');

const base = process.argv[2] || 'http://127.0.0.1:9333';
const targetUrl = process.argv[3] || 'http://127.0.0.1:8787/avatar_3d.html?config=models/spirit3d/manifest.json&v=cdp-debug';
const waitMs = Number(process.argv[4] || 30000);
const screenshotPath = process.argv[5] || '';

function getJson(url, method = 'GET') {
  return new Promise((resolve, reject) => {
    const req = http.request(url, { method }, res => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', chunk => body += chunk);
      res.on('end', () => {
        try { resolve(JSON.parse(body)); } catch (err) { reject(new Error(`${method} ${url}: ${body.slice(0, 240)}`)); }
      });
    });
    req.on('error', reject);
    req.end();
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

class MiniWebSocket extends EventEmitter {
  constructor(url) {
    super();
    this.url = new URL(url);
    this.socket = null;
    this.buffer = Buffer.alloc(0);
    this.handshakeDone = false;
  }

  connect() {
    const key = crypto.randomBytes(16).toString('base64');
    this.socket = net.connect(Number(this.url.port || 80), this.url.hostname, () => {
      const path = `${this.url.pathname}${this.url.search}`;
      this.socket.write(
        [
          `GET ${path} HTTP/1.1`,
          `Host: ${this.url.host}`,
          'Upgrade: websocket',
          'Connection: Upgrade',
          `Sec-WebSocket-Key: ${key}`,
          'Sec-WebSocket-Version: 13',
          '',
          '',
        ].join('\r\n')
      );
    });
    this.socket.on('data', data => this._onData(data));
    this.socket.on('error', err => this.emit('error', err));
    this.socket.on('close', () => this.emit('close'));
  }

  send(text) {
    const payload = Buffer.from(text);
    const mask = crypto.randomBytes(4);
    let header;
    if (payload.length < 126) {
      header = Buffer.from([0x81, 0x80 | payload.length]);
    } else if (payload.length < 65536) {
      header = Buffer.alloc(4);
      header[0] = 0x81;
      header[1] = 0x80 | 126;
      header.writeUInt16BE(payload.length, 2);
    } else {
      header = Buffer.alloc(10);
      header[0] = 0x81;
      header[1] = 0x80 | 127;
      header.writeBigUInt64BE(BigInt(payload.length), 2);
    }
    const masked = Buffer.alloc(payload.length);
    for (let i = 0; i < payload.length; i += 1) masked[i] = payload[i] ^ mask[i % 4];
    this.socket.write(Buffer.concat([header, mask, masked]));
  }

  close() {
    this.socket?.end();
  }

  _onData(data) {
    this.buffer = Buffer.concat([this.buffer, data]);
    if (!this.handshakeDone) {
      const end = this.buffer.indexOf('\r\n\r\n');
      if (end === -1) return;
      const head = this.buffer.slice(0, end).toString('utf8');
      if (!head.startsWith('HTTP/1.1 101')) {
        this.emit('error', new Error(`WebSocket handshake failed: ${head}`));
        return;
      }
      this.buffer = this.buffer.slice(end + 4);
      this.handshakeDone = true;
      this.emit('open');
    }
    this._readFrames();
  }

  _readFrames() {
    while (this.buffer.length >= 2) {
      const first = this.buffer[0];
      const second = this.buffer[1];
      const opcode = first & 0x0f;
      let length = second & 0x7f;
      let offset = 2;
      if (length === 126) {
        if (this.buffer.length < 4) return;
        length = this.buffer.readUInt16BE(2);
        offset = 4;
      } else if (length === 127) {
        if (this.buffer.length < 10) return;
        length = Number(this.buffer.readBigUInt64BE(2));
        offset = 10;
      }
      const masked = Boolean(second & 0x80);
      const maskOffset = masked ? 4 : 0;
      if (this.buffer.length < offset + maskOffset + length) return;
      let payload = this.buffer.slice(offset + maskOffset, offset + maskOffset + length);
      if (masked) {
        const mask = this.buffer.slice(offset, offset + 4);
        payload = Buffer.from(payload.map((byte, index) => byte ^ mask[index % 4]));
      }
      this.buffer = this.buffer.slice(offset + maskOffset + length);
      if (opcode === 0x1) this.emit('message', { data: payload.toString('utf8') });
      if (opcode === 0x8) this.close();
    }
  }
}

async function main() {
  const page = await getJson(`${base}/json/new?${encodeURIComponent(targetUrl)}`, 'PUT');
  const ws = new MiniWebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const events = [];

  function send(method, params = {}) {
    const callId = ++id;
    ws.send(JSON.stringify({ id: callId, method, params }));
    return new Promise((resolve, reject) => {
      pending.set(callId, { resolve, reject, method });
      setTimeout(() => {
        if (pending.has(callId)) {
          pending.delete(callId);
          reject(new Error(`CDP timeout: ${method}`));
        }
      }, 10000);
    });
  }

  ws.on('message', event => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) p.reject(new Error(`${p.method}: ${msg.error.message}`));
      else p.resolve(msg.result);
      return;
    }
    if (msg.method === 'Runtime.consoleAPICalled') {
      events.push({
        type: 'console',
        level: msg.params.type,
        text: msg.params.args.map(a => a.value ?? a.description ?? '').join(' '),
      });
    } else if (msg.method === 'Runtime.exceptionThrown') {
      events.push({
        type: 'exception',
        text: msg.params.exceptionDetails?.text,
        description: msg.params.exceptionDetails?.exception?.description,
      });
    } else if (msg.method === 'Network.loadingFailed') {
      events.push({
        type: 'network_failed',
        url: msg.params.requestId,
        errorText: msg.params.errorText,
        blockedReason: msg.params.blockedReason || '',
      });
    } else if (msg.method === 'Network.responseReceived') {
      const url = msg.params.response?.url || '';
      if (url.includes('SpiritKinAI') || url.includes('manifest.json') || url.includes('unpkg.com') || url.includes('esm.sh')) {
        events.push({
          type: 'response',
          status: msg.params.response.status,
          url,
          mimeType: msg.params.response.mimeType,
        });
      }
    }
  });

  await new Promise((resolve, reject) => {
    ws.once('open', resolve);
    ws.once('error', reject);
    ws.connect();
  });
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Network.enable');
  await send('Page.navigate', { url: targetUrl });
  await sleep(waitMs);
  const evalResult = await send('Runtime.evaluate', {
    expression: `(() => ({
      status: document.querySelector('#status')?.innerText,
      chat: [...document.querySelectorAll('#chat .msg')].map(n=>n.innerText),
      modelUrl: document.querySelector('#modelUrl')?.value,
      canvas: (() => {
        const c=document.querySelector('canvas');
        if(!c) return null;
        return {width:c.width,height:c.height,clientWidth:c.clientWidth,clientHeight:c.clientHeight};
      })()
    }))()`,
    returnByValue: true,
  });
  if (screenshotPath) {
    const shot = await send('Page.captureScreenshot', { format: 'png', fromSurface: true });
    fs.writeFileSync(screenshotPath, Buffer.from(shot.data, 'base64'));
  }
  console.log(JSON.stringify({ page: evalResult.result.value, events }, null, 2));
  ws.close();
}

main().catch(err => {
  console.error(err.stack || err.message || String(err));
  process.exit(1);
});

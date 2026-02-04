const fsx = require("fs");

const deStringPath = process.env.DESTRING_PATH;
if (!deStringPath) {
  console.error("DESTRING_PATH is not set");
  process.exit(2);
}

const code = fsx.readFileSync(deStringPath, "utf8");
const match = code.match(
  /data:application\/octet-stream;base64,([A-Za-z0-9+/=]+)/,
);
const wasmBytes = match ? Buffer.from(match[1], "base64") : null;
const input = fsx.readFileSync(0, "utf8").trim();

let patched = code.replace(
  "Module.onRuntimeInitialized = function() {Module.isReady = true;}",
  'Module.onRuntimeInitialized = function() {Module.isReady = true; if (typeof __onReady === "function") { __onReady(__decodeInput); }}',
);

let done = false;
var Module = { wasmBinary: wasmBytes };
var __decodeInput = input;
var __onReady = function (text) {
  if (done) return;
  done = true;
  const outPtr = Module._DeString(allocateUTF8(text));
  const decodedStr = UTF8ToString(outPtr);
  process.stdout.write(decodedStr);
};

var window = globalThis;
var self = globalThis;
var global = globalThis;

// Execute deString.js
// eslint-disable-next-line no-eval
eval(patched);

if (Module.isReady && !done) {
  __onReady(__decodeInput);
}

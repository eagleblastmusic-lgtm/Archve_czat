/**
 * Regression Test Suite for Cross-Session Lifecycle (Frontend)
 *
 * Covers:
 * 1. Consumer identity uniqueness per tab/manager instance.
 * 2. ensureStoryboard attaches consumer_id to build requests.
 * 3. Polling loop sends lease renewal heartbeats with consumer_id.
 * 4. unsubscribe triggers releaseLease with consumer_id when local subscribers reach 0.
 * 5. Player QoS hardCancel forwards consumer_id to avoid killing concurrent tabs.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

function setupTestEnvironment() {
  const previewCode = fs.readFileSync(path.join(__dirname, '..', 'static', 'timeline-preview.js'), 'utf8');
  const qosCode = fs.readFileSync(path.join(__dirname, '..', 'static', 'v452-player-qos.js'), 'utf8');

  let v2Calls = 0;
  const fetchCalls = [];
  const fakeFetch = async (url, opts = {}) => {
    fetchCalls.push({ url: String(url), method: opts.method || 'GET', headers: opts.headers || {} });
    if (url.includes('/api/storyboard/v2/build')) {
      return { ok: true, json: async () => ({ status: 'queued' }) };
    }
    if (url.includes('/api/storyboard/v2/lease/renew')) {
      return { ok: true, json: async () => ({ ok: true, active_consumers: 1 }) };
    }
    if (url.includes('/api/storyboard/v2?')) {
      v2Calls++;
      return {
        ok: true,
        json: async () => ({
          version: 12,
          rev: v2Calls,
          complete: v2Calls >= 4,
          sheets: [],
          frames: [],
        }),
      };
    }
    return { ok: true, json: async () => ({ ok: true }) };
  };

  const listenersMap = new Map();
  const makeElement = () => ({
    style: {},
    dataset: {},
    classList: { add: () => {}, remove: () => {} },
    setAttribute: () => {},
    removeAttribute: () => {},
    getAttribute: () => '',
    addEventListener: (type, fn) => {
      if (!listenersMap.has(type)) listenersMap.set(type, []);
      listenersMap.get(type).push(fn);
    },
    removeEventListener: (type, fn) => {
      const arr = listenersMap.get(type) || [];
      listenersMap.set(type, arr.filter(f => f !== fn));
    },
    pause: () => {},
    load: () => {},
    play: () => Promise.resolve(),
    replaceChildren: () => {},
    appendChild: () => {},
    querySelectorAll: () => [],
    querySelector: () => null,
  });

  const sandbox = {
    console,
    Date,
    Math,
    Set,
    Map,
    Promise,
    setTimeout,
    clearTimeout,
    AbortController,
    performance: { now: () => Date.now() },
    fetch: fakeFetch,
    document: {
      hidden: false,
      body: { style: {} },
      querySelector: () => null,
      createElement: makeElement,
      addEventListener: (type, fn) => {
        if (!listenersMap.has(type)) listenersMap.set(type, []);
        listenersMap.get(type).push(fn);
      },
    },
    addEventListener: () => {},
    Image: class {
      constructor() {
        this.src = '';
      }
      decode() {
        return Promise.resolve();
      }
    },
    window: {},
    globalThis: {},
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(previewCode, sandbox);
  vm.runInContext(qosCode, sandbox);

  return { sandbox, fetchCalls, makeElement };
}

async function testConsumerIdentityAndBuild() {
  const { sandbox, fetchCalls } = setupTestEnvironment();
  const manager = sandbox.ArchivebateTimelinePreview.manager;

  assert(manager.consumerId, "Manager must generate a non-empty consumerId");
  assert(manager.consumerId.startsWith("tab_"), "consumerId must start with 'tab_' prefix");

  // Call ensureStoryboard
  const key = "archivebate:id:vid_101";
  const p = manager.ensureStoryboard(key, "vid_101", 120.0, "archivebate");

  // Allow promise tick to trigger initial fetch
  await new Promise(r => setTimeout(r, 10));

  // Find build request
  const buildCall = fetchCalls.find(c => c.url.includes('/api/storyboard/v2/build'));
  assert(buildCall, "ensureStoryboard must issue /api/storyboard/v2/build request");
  assert(buildCall.url.includes(`consumer_id=${encodeURIComponent(manager.consumerId)}`),
    `Build call URL must include consumer_id. Got: ${buildCall.url}`);

  manager.stopPolling(key, "archivebate");
  console.log("PASS: Consumer identity generated and attached to /build request");
}

async function testLeaseHeartbeatDuringPolling() {
  const { sandbox, fetchCalls } = setupTestEnvironment();
  const manager = sandbox.ArchivebateTimelinePreview.manager;
  const key = "archivebate:id:vid_hb";

  const p = manager.ensureStoryboard(key, "vid_hb", 120.0, "archivebate");

  // Wait for several poll loop iterations
  await new Promise(r => setTimeout(r, 50));

  const renewCall = fetchCalls.find(c => c.url.includes('/api/storyboard/v2/lease/renew'));
  assert(renewCall, "ensureStoryboard must issue /lease/renew heartbeat during active polling");
  assert(renewCall.url.includes(`consumer_id=${encodeURIComponent(manager.consumerId)}`),
    `Renew call URL must include consumer_id. Got: ${renewCall.url}`);

  manager.stopPolling(key, "archivebate");
  console.log("PASS: Lease renewal heartbeat issued during active polling");
}

async function testUnsubscribeReleasesLease() {
  const { sandbox, fetchCalls } = setupTestEnvironment();
  const manager = sandbox.ArchivebateTimelinePreview.manager;
  const key = "archivebate:id:vid_unsub";

  manager.subscribe(key, "subscriber_1", () => {}, "archivebate");
  assert(manager.hasSubscribers(key, "archivebate") === true);

  fetchCalls.length = 0;
  manager.unsubscribe(key, "subscriber_1", "archivebate");
  assert(manager.hasSubscribers(key, "archivebate") === false);

  const releaseCall = fetchCalls.find(c => c.url.includes('/api/runtime/v452/storyboard/cancel'));
  assert(releaseCall, "unsubscribe must issue cancel/release call when last subscriber unregisters");
  assert(releaseCall.url.includes(`consumer_id=${encodeURIComponent(manager.consumerId)}`),
    `Cancel call URL must include consumer_id. Got: ${releaseCall.url}`);
  assert(releaseCall.url.includes("reason=unsubscribe"),
    `Cancel call must specify reason=unsubscribe. Got: ${releaseCall.url}`);

  console.log("PASS: Unsubscribe sends consumer-scoped releaseLease request");
}

async function testPlayerQoSHardCancelIncludesConsumerId() {
  const { sandbox, fetchCalls } = setupTestEnvironment();
  const qos = sandbox.ArchivebatePlayerQoS;
  const manager = sandbox.ArchivebateTimelinePreview.manager;

  const mockVideo = { id: 'modalVideo', dataset: { videoId: 'vid_qos_cancel' } };
  fetchCalls.length = 0;

  qos.hardCancel(mockVideo, "user_switch");

  const cancelCall = fetchCalls.find(c => c.url.includes('/api/runtime/v452/storyboard/cancel'));
  assert(cancelCall, "hardCancel must issue cancel request");
  assert(cancelCall.url.includes(`consumer_id=${encodeURIComponent(manager.consumerId)}`),
    `hardCancel call URL must include consumer_id. Got: ${cancelCall.url}`);
  assert(cancelCall.url.includes("id=vid_qos_cancel"),
    `hardCancel must specify video id. Got: ${cancelCall.url}`);

  console.log("PASS: Player QoS hardCancel passes consumer_id to prevent multi-tab interruption");
}

async function run() {
  console.log("======================================================================");
  console.log("RUNNING CROSS-SESSION FRONTEND LIFECYCLE TESTS");
  console.log("======================================================================");
  await testConsumerIdentityAndBuild();
  await testLeaseHeartbeatDuringPolling();
  await testUnsubscribeReleasesLease();
  await testPlayerQoSHardCancelIncludesConsumerId();
  console.log("======================================================================");
  console.log("ALL CROSS-SESSION FRONTEND TESTS PASSED (GREEN)!");
  console.log("======================================================================");
}

run().catch(err => {
  console.error("TEST FAILED:", err);
  process.exit(1);
});

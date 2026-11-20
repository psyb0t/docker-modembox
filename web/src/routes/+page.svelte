<script lang="ts">
  import { onMount } from "svelte";
  import Hooks from "./Hooks.svelte";
  import { emptyHooks, type HookState } from "./hooks";

  type Call = {state: string; direction: string; peer: string};
  type Device = {id: string; connected: boolean; model?: string; manufacturer?: string; firmware?: string;
    imei?: string; iccid?: string; profile?: string; sim?: string; pinSaved?: boolean; calls: Call[];
    capabilities: Record<string, boolean | string>; operator?: string; signal?: string;
    registration?: string; packetAttached?: string; audio?: boolean; playing?: boolean;
    microphone?: boolean; mediaError?: string};
  type Message = {id: number; modem: string; direction: string; peer: string; body: string;
    created: string; status: string; replaces?: number[]};
  type Activity = {seq: number; source: string; modem: string; action: string; outcome: string; time: number};
  type Clip = {name: string; wav: string};
  let token = "", draftToken = "", connection = "Disconnected", selected = "", error = "";
  let tokenRequired = true;
  let devices: Device[] = [], messages: Message[] = [], activity: Activity[] = [], clips: Clip[] = [];
  let smsNumber = "", smsText = "", dialNumber = "", pin = "", remember = false, command = "AT+CSQ";
  let atReply = "", busy = false, filter = "all", older = true;
  let allModemMessages = false;
  let hooks = emptyHooks;
  let screen: "console" | "hooks" = "console";
  let events: WebSocket | undefined, audio: WebSocket | undefined;
  let reconnect: ReturnType<typeof setTimeout> | undefined;
  let context: AudioContext | undefined, microphone: MediaStream | undefined;
  let inputNode: AudioWorkletNode | undefined, inputSource: MediaStreamAudioSourceNode | undefined;
  let listening = false, mic = false, nextAudio = 0, audioDevice = "";
  let device: Device | undefined;
  let visible: Message[] = [];
  let call: Call | undefined;
  $: device = devices.find(item => item.id === selected);
  $: if (!devices.some(item => item.id === selected)) selected = devices[0]?.id ?? "";
  $: call = device?.calls[0];
  $: visible = messages.filter(message => (allModemMessages || !selected || message.modem === selected) &&
    (filter === "all" || message.direction === filter));
  $: if (listening && (!device?.audio || selected !== audioDevice)) stopAudio();

  function wsURL(path: string) {
    const url = new URL(path, location.href);
    url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    return url.href;
  }

  function connect() {
    if (tokenRequired && !token) return;
    clearTimeout(reconnect);
    events?.close();
    connection = "Connecting";
    const socket = new WebSocket(wsURL("/v1/events"));
    events = socket;
    if (tokenRequired) socket.onopen = () => socket.send(token);
    socket.onmessage = event => {
      const frame = JSON.parse(event.data);
      if (frame.type === "snapshot") {
        devices = frame.data.modems;
        messages = frame.data.messages;
        activity = frame.data.activity;
        hooks = frame.data.hooks ?? emptyHooks;
        connection = "Live";
      } else if (frame.type === "modem") {
        devices = [...devices.filter(item => item.id !== frame.data.id), frame.data]
          .sort((left, right) => left.id.localeCompare(right.id));
      } else if (frame.type === "message") {
        const replaced = new Set([frame.data.id, ...(frame.data.replaces ?? [])]);
        messages = [frame.data, ...messages.filter(item => !replaced.has(item.id))]
          .sort((left, right) => right.id - left.id);
      } else if (frame.type === "activity") {
        activity = [{...frame.data, seq: frame.seq}, ...activity].slice(0, 60);
      } else if (frame.type === "hook.rules") {
        hooks = {...hooks, rules: patchRows(hooks.rules, frame.data)};
      } else if (frame.type === "hook.destinations") {
        hooks = {...hooks, destinations: patchRows(hooks.destinations, frame.data)};
      } else if (frame.type === "hook.runs") {
        hooks = {...hooks, runs: patchRows(hooks.runs, frame.data).sort((left, right) => right.id - left.id)};
      } else if (frame.type === "hook.calls") {
        hooks = {...hooks, calls: patchRows(hooks.calls, frame.data).sort((left, right) => right.time - left.time)};
      }
    };
    socket.onclose = event => {
      if (events !== socket) return;
      connection = event.code === 4401 ? "Token rejected" : "Disconnected";
      if (event.code !== 4401) reconnect = setTimeout(connect, 1500);
    };
  }

  function patchRows<Row extends HookState[keyof HookState][number]>(rows: Row[], changed: Row & {deleted?: boolean}) {
    if (changed.deleted) return rows.filter(row => row.id !== changed.id);
    return rows.some(row => row.id === changed.id) ?
      rows.map(row => row.id === changed.id ? changed : row) : [changed, ...rows];
  }

  function signIn(event: SubmitEvent) {
    event.preventDefault();
    token = draftToken;
    draftToken = "";
    sessionStorage.setItem("modembox_token", token);
    connect();
  }

  function signOut() {
    token = "";
    sessionStorage.removeItem("modembox_token");
    clearTimeout(reconnect);
    const socket = events;
    events = undefined;
    socket?.close();
    stopAudio();
    devices = [];
    messages = [];
    activity = [];
    hooks = emptyHooks;
    screen = "console";
    clips = [];
    selected = "";
    connection = "Disconnected";
  }

  async function api(path: string, method = "GET", data?: unknown) {
    const headers: Record<string, string> = {};
    if (tokenRequired) headers.Authorization = "Bearer " + token;
    const hasBody = method === "POST" || method === "PUT";
    if (hasBody) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {method, headers,
      body: hasBody ? JSON.stringify(data ?? {}) : undefined});
    const result = await response.json();
    if (!response.ok) throw new Error(result.message ?? "Request failed");
    return result;
  }

  async function act(path: string, data?: unknown, method = "POST") {
    if (!selected || busy) return;
    busy = true;
    error = "";
    try {
      return await api("/v1/modems/" + encodeURIComponent(selected) + path, method, data);
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      busy = false;
    }
  }

  async function loadOlder() {
    try {
      const result = await api("/v1/messages?before=" + (messages.at(-1)?.id ?? 1));
      const existing = new Set(messages.map(message => message.id));
      messages = [...messages, ...result.items.filter((item: Message) => !existing.has(item.id))];
      older = result.next !== null;
    } catch (cause) {
      error = String(cause);
    }
  }

  async function submitSms(event: SubmitEvent) {
    event.preventDefault();
    const result = await act("/sms", {number: smsNumber, text: smsText});
    if (result?.status === "submitted") smsText = "";
  }

  async function submitPin(event: SubmitEvent) {
    event.preventDefault();
    const value = pin;
    pin = "";
    await act("/pin", {pin: value, remember});
  }

  async function runAT(event: SubmitEvent) {
    event.preventDefault();
    const result = await act("/at", {command});
    if (result) atReply = [...result.lines, result.terminal].join("\n");
  }

  async function startAudio() {
    if (listening || !device?.audio) return;
    try {
      const ctx = new AudioContext({sampleRate: 8000});
      context = ctx;
      await ctx.resume();
      audioDevice = selected;
      const socket = new WebSocket(wsURL("/v1/modems/" + encodeURIComponent(selected) + "/audio/ws"));
      socket.binaryType = "arraybuffer";
      audio = socket;
      nextAudio = 0;
      listening = true;
      if (tokenRequired) socket.onopen = () => socket.send(token);
      socket.onclose = () => { if (audio === socket) stopAudio(); };
      socket.onmessage = event => {
        if (!(event.data instanceof ArrayBuffer)) return;
        const pcm = new Int16Array(event.data);
        if (!pcm.length) return;
        const output = ctx.createBuffer(1, pcm.length, 8000);
        const track = output.getChannelData(0);
        for (let index = 0; index < track.length; index++) track[index] = pcm[index] / 32768;
        if (nextAudio > ctx.currentTime + .25) return;
        if (nextAudio < ctx.currentTime) nextAudio = ctx.currentTime + .04;
        const source = ctx.createBufferSource();
        source.buffer = output;
        source.connect(ctx.destination);
        source.start(nextAudio);
        nextAudio += output.duration;
      };
    } catch (cause) {
      stopAudio();
      error = String(cause);
    }
  }

  async function toggleMic() {
    if (mic) { stopMic(); return; }
    if (device?.microphone || !audio || !context || audio.readyState !== WebSocket.OPEN) return;
    try {
      if (!navigator.mediaDevices) throw new Error("Microphone access needs HTTPS or localhost");
      const ctx = context, socket = audio;
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true},
      });
      if (audio !== socket || context !== ctx) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }
      microphone = stream;
      await ctx.audioWorklet.addModule("/microphone.js");
      if (audio !== socket || context !== ctx) return;
      inputNode = new AudioWorkletNode(ctx, "modembox-microphone");
      const muted = ctx.createGain();
      muted.gain.value = 0;
      inputNode.connect(muted).connect(ctx.destination);
      inputNode.port.onmessage = event => {
        if (socket.readyState === WebSocket.OPEN && socket.bufferedAmount < 3840) socket.send(event.data);
      };
      inputSource = ctx.createMediaStreamSource(stream);
      inputSource.connect(inputNode);
      socket.send("mic:on");
      mic = true;
    } catch (cause) {
      stopMic();
      error = String(cause);
    }
  }

  function stopMic() {
    if (mic && audio?.readyState === WebSocket.OPEN) audio.send("mic:off");
    inputNode?.disconnect();
    inputSource?.disconnect();
    microphone?.getTracks().forEach(track => track.stop());
    inputNode = undefined;
    inputSource = undefined;
    microphone = undefined;
    mic = false;
  }

  function stopAudio() {
    stopMic();
    const socket = audio;
    audio = undefined;
    socket?.close();
    if (context) void context.close();
    context = undefined;
    listening = false;
  }

  async function addClip(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    input.value = "";
    if (!file) return;
    try {
      if (file.size > 16_000_000) throw new Error("WAV file exceeds 16 MB");
      const work = new AudioContext();
      let decoded: AudioBuffer;
      try { decoded = await work.decodeAudioData(await file.arrayBuffer()); }
      finally { await work.close(); }
      if (decoded.duration < .01 || decoded.duration > 30) {
        throw new Error("Choose a WAV file between 10 ms and 30 seconds");
      }
      const count = Math.ceil(decoded.duration * 8000);
      const render = new OfflineAudioContext(1, count, 8000);
      const source = render.createBufferSource();
      source.buffer = decoded;
      source.connect(render.destination);
      source.start();
      const mono = (await render.startRendering()).getChannelData(0);
      const wav = new Uint8Array(44 + mono.length * 2);
      const view = new DataView(wav.buffer);
      for (const [offset, label] of [[0, "RIFF"], [8, "WAVEfmt "], [36, "data"]] as const) {
        wav.set(new TextEncoder().encode(label), offset);
      }
      for (const [offset, value] of [[4, wav.length - 8], [16, 16], [24, 8000],
        [28, 16000], [40, mono.length * 2]]) view.setUint32(offset, value, true);
      for (const [offset, value] of [[20, 1], [22, 1], [32, 2], [34, 16]]) {
        view.setUint16(offset, value, true);
      }
      for (let index = 0; index < mono.length; index++) {
        view.setInt16(44 + index * 2, Math.round(Math.max(-1, Math.min(1, mono[index])) * 32767), true);
      }
      const reader = new FileReader();
      reader.onload = () => {
        const encoded = String(reader.result).split(",", 2)[1];
        if (encoded) clips = [...clips.filter(clip => clip.name !== file.name), {name: file.name, wav: encoded}];
      };
      reader.readAsDataURL(new Blob([wav], {type: "audio/wav"}));
    } catch (cause) {
      error = String(cause);
    }
  }

  onMount(() => {
    token = sessionStorage.getItem("modembox_token") ?? "";
    const startup = new AbortController();
    fetch("/auth-mode", {signal: startup.signal})
      .then(async response => {
        if (response.ok) tokenRequired = (await response.json()).tokenRequired !== false;
      })
      .catch(() => { /* Keep token auth when the mode cannot be loaded. */ })
      .finally(() => { if (!startup.signal.aborted) connect(); });
    return () => {
      startup.abort();
      clearTimeout(reconnect);
      const socket = events;
      events = undefined;
      socket?.close();
      stopAudio();
    };
  });
</script>

<svelte:head>
  <title>ModemBox</title>
  <meta name="description" content="USB modem console for SMS, voice and AT commands" />
</svelte:head>

<main>
  <header>
    <div><span class="eyebrow">CELLULAR CONSOLE</span><h1>ModemBox<span>.</span></h1></div>
    <div class="header-actions">
      {#if !tokenRequired || (token && connection !== "Token rejected")}
        <nav class="screen-nav" aria-label="Console screens">
          <button class:chosen={screen === "console"} onclick={() => (screen = "console")}>Console</button>
          <button class:chosen={screen === "hooks"} onclick={() => (screen = "hooks")}>Hooks</button>
        </nav>
      {/if}
      <div class="status"><i class:online={connection === "Live"}></i>{connection}
        {#if tokenRequired && token}<button onclick={signOut}>Disconnect</button>{/if}
      </div>
    </div>
  </header>

  {#if tokenRequired && (!token || connection === "Token rejected")}
    <section class="login">
      <h2>Connect to ModemBox</h2>
      <form onsubmit={signIn}>
        <label>Access token <input type="password" bind:value={draftToken} autocomplete="off" required /></label>
        <button class="primary">Connect</button>
      </form>
    </section>
  {:else}
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    {#if screen === "hooks"}
      <Hooks {hooks} modems={devices} {messages} request={api} />
    {:else}
    <nav aria-label="Modems">
      {#each devices as item (item.id)}
        <button class:chosen={item.id === selected} onclick={() => (selected = item.id)}>
          <i class:online={item.connected}></i>{item.model || item.id}
        </button>
      {:else}<span class="muted">No modem detected. Check the USB connection and device mapping.</span>{/each}
    </nav>

    <div class="grid">
      <div class="column">
        {#if device}
          <section>
            <div class="heading"><h2>Device</h2><span class="tag">{device.profile ?? "offline"}</span></div>
            <dl>
              {#each [["Model", device.model], ["Maker", device.manufacturer], ["Firmware", device.firmware],
                ["IMEI", device.imei], ["ICCID", device.iccid], ["SIM", device.sim], ["Operator", device.operator],
                ["Signal (CSQ)", device.signal], ["Registration", device.registration], ["Packet attach", device.packetAttached]] as [label, value]}
                <div><dt>{label}</dt><dd>{value || "—"}</dd></div>
              {/each}
            </dl>
            <div class="features">
              {#each Object.entries(device.capabilities) as [name, value]}
                <span class="tag">{name}: {String(value)}</span>
              {/each}
            </div>
            {#if device.sim === "SIM PIN"}
              <form onsubmit={submitPin}>
                <label>SIM PIN <input type="password" bind:value={pin} pattern={"[0-9]{4,8}"} required /></label>
                <label class="checkbox"><input type="checkbox" bind:checked={remember} /> Remember this SIM</label>
                <button class="primary" disabled={busy}>Unlock</button>
              </form>
            {/if}
            {#if device.pinSaved}
              <button disabled={busy} onclick={() => act("/pin", undefined, "DELETE")}>Forget saved PIN</button>
            {/if}
          </section>

          <section>
            <div class="heading"><h2>Voice</h2><span class="tag">{call?.state ?? "Idle"}</span></div>
            {#if call}
              <p>{call.direction} {call.peer || "unknown number"}</p>
              <div class="actions">
                {#if call.state === "incoming"}<button class="primary" disabled={busy} onclick={() => act("/calls/answer")}>Answer</button>{/if}
                <button class="danger" disabled={busy} onclick={() => act("/calls/current", undefined, "DELETE")}>Hang up</button>
                <button disabled={!device.audio} onclick={() => listening ? stopAudio() : startAudio()}>
                  {listening ? "Stop listening" : "Listen"}
                </button>
                <button disabled={!listening || Boolean(device.microphone && !mic)} onclick={toggleMic}>
                  {mic ? "Mute mic" : device.microphone ? "Mic in use" : "Enable mic"}
                </button>
              </div>
            {:else}
              <form onsubmit={(event) => { event.preventDefault(); void act("/calls", {number: dialNumber}); }}>
                <label>Number <input type="tel" bind:value={dialNumber} required pattern={"[+]?[0-9]{3,20}"} /></label>
                <button class="primary" disabled={busy || !device.connected || device.capabilities.voice === "unsupported"}>Dial</button>
              </form>
            {/if}
            {#if device.mediaError}<p class="error">{device.mediaError}</p>{/if}
            <div class="clipbox">
              <label>Soundboard <input type="file" accept=".wav,audio/wav" onchange={addClip} /></label>
              {#each clips as clip (clip.name)}
                <div class="clip"><span>{clip.name}</span>
                  <button disabled={!device.audio || busy} onclick={() => act("/audio", {wav: clip.wav})}>Play</button>
                </div>
              {/each}
              {#if device.playing}
                <button disabled={busy} onclick={() => act("/audio", undefined, "DELETE")}>Stop playback</button>
              {/if}
            </div>
          </section>

          <section>
            <h2>AT console</h2>
            <form onsubmit={runAT}>
              <label>Command <input class="mono" bind:value={command} required maxlength="128" /></label>
              <button disabled={busy || !device.connected}>Run</button>
            </form>
            {#if atReply}<pre>{atReply}</pre>{/if}
          </section>
        {/if}
      </div>

      <div class="column">
        <section>
          <div class="heading"><h2>Messages</h2>
            {#if selected}
              <button onclick={() => (allModemMessages = !allModemMessages)}>
                {allModemMessages ? "Selected modem" : "All modems"}
              </button>
            {/if}
            <select aria-label="Message direction" bind:value={filter}>
              <option value="all">All</option><option value="incoming">Inbox</option><option value="outgoing">Outbox</option>
            </select>
          </div>
          {#if device?.capabilities.sms}
            <form class="compose" onsubmit={submitSms}>
              <label>To <input type="tel" bind:value={smsNumber} required pattern={"[+]?[0-9]{3,20}"} /></label>
              <label>Message <textarea bind:value={smsText} maxlength="16830" required></textarea></label>
              <button class="primary" disabled={busy}>Send SMS</button>
            </form>
          {/if}
          {#each visible as message (message.id)}
            <article>
              <div class="heading"><strong>{message.peer}</strong><time>{new Date(message.created).toLocaleString()}</time></div>
              <p>{message.body}</p><span class="tag">{message.direction} · {message.status}</span>
            </article>
          {:else}<p class="muted">No messages.</p>{/each}
          {#if older && messages.length >= 50}<button onclick={loadOlder}>Load older</button>{/if}
        </section>
        <section>
          <h2>Live activity</h2>
          <ul>
            {#each activity as item (item.seq)}
              <li><span class="tag">{item.source}</span> {item.action.replaceAll("_", " ")}
                <small>{item.outcome} · {new Date(item.time * 1000).toLocaleTimeString()}</small></li>
            {:else}<li class="muted">No activity yet.</li>{/each}
          </ul>
        </section>
      </div>
    </div>
    {/if}
  {/if}
</main>

<style>
  :global(*){box-sizing:border-box}
  :global(body){margin:0;background:#101313;color:#e8eeea;font:15px/1.55 system-ui,sans-serif}
  main{max-width:1200px;margin:auto;padding:2rem clamp(1rem,4vw,3rem)}
  header,.heading,.clip{display:flex;justify-content:space-between;align-items:center;gap:1rem}
  header{border-bottom:1px solid #353e3a;padding-bottom:1.25rem;margin-bottom:1.3rem}
  .eyebrow,.mono,.tag,pre{font-family:ui-monospace,monospace}
  .eyebrow{letter-spacing:.18em;color:#8da098;font-size:.7rem}
  h1{font-size:clamp(2rem,5vw,3rem);line-height:1;margin:.35rem 0 0}
  h1 span{color:#7af5ac}
  h2{margin:0 0 .45rem;font-size:1.05rem;letter-spacing:.035em}
  .status,nav,.actions,form,.features{display:flex;align-items:end;flex-wrap:wrap;gap:.65rem}
  .status{align-items:center}
  i{display:inline-block;width:.5rem;height:.5rem;border-radius:50%;background:#8b9690;margin-right:.35rem}
  i.online{background:#7af5ac}
  nav{margin-bottom:1rem}
  .header-actions{display:flex;align-items:center;justify-content:end;flex-wrap:wrap;gap:.85rem}
  .screen-nav{margin:0}
  .grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.2fr);gap:1rem}
  .column{display:grid;gap:1rem;align-content:start;min-width:0}
  section{background:#181e1c;border:1px solid #353e3a;border-radius:.7rem;padding:1rem;min-width:0}
  .login{max-width:32rem}
  button,input,textarea,select{font:inherit;color:inherit;background:#111614;border:1px solid #46554e;border-radius:.35rem;padding:.45rem .65rem}
  button{cursor:pointer}
  button:disabled{opacity:.45;cursor:default}
  button:not(:disabled):hover,.chosen{border-color:#7af5ac}
  .primary{background:#7af5ac;color:#13251a;border-color:#7af5ac;font-weight:700}
  .danger,.error{color:#ffaaa5}
  label{display:grid;gap:.25rem;color:#b3c2ba;font-size:.86rem}
  .checkbox{display:flex;align-items:center;min-height:2.35rem}
  .compose{align-items:stretch;flex-direction:column;border-bottom:1px solid #353e3a;padding:.5rem 0 1rem}
  .compose button{align-self:start}
  textarea{resize:vertical;min-height:3.6rem}
  .tag{font-size:.73rem;color:#96debd;background:#22342c;border-radius:.25rem;padding:.12rem .35rem;overflow-wrap:anywhere}
  dl{display:grid;grid-template-columns:1fr 1fr;gap:.55rem}
  dt,.muted,small,time{color:#9baca4}
  dt{font-size:.78rem}
  dd{margin:0;overflow-wrap:anywhere}
  .features,.clipbox{margin-top:.75rem;margin-bottom:.6rem}
  .clip{padding:.4rem 0;overflow-wrap:anywhere}
  article{border-bottom:1px solid #353e3a;padding:.75rem 0;overflow-wrap:anywhere}
  article p{white-space:pre-wrap;margin:.3rem 0}
  time,small{font-size:.75rem}
  pre{white-space:pre-wrap;overflow-wrap:anywhere}
  ul{margin:0;padding:0;list-style:none}
  li{padding:.35rem 0}
  li small{margin-left:.4rem}
  @media(max-width:720px){.grid{grid-template-columns:1fr}header{align-items:flex-start}.status{justify-content:end}}
</style>

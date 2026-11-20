<script lang="ts">
  import type { Action, Destination, HookState, Rule } from "./hooks";

  export let hooks: HookState;
  export let modems: {id: string; model?: string}[];
  export let messages: {id: number; direction: string; peer: string; body: string}[];
  export let request: (path: string, method?: string, data?: unknown) => Promise<any>;

  const triggers = {
    "sms.received": "SMS received", "call.incoming": "Incoming call", "call.ended": "Call ended"
  };
  const labels = {sms: "Send SMS", http: "Make HTTP request", decline: "Decline call"};
  const fields = ["id", "type", "modem", "time", "peer", "text", "outcome", "callerId", "messageId", "callId"];
  let tab: "rules" | "destinations" | "runs" | "calls" = "rules";
  let rule: Rule | undefined;
  let destination: Destination | undefined;
  let headers = "", signingKey = "", previewId = "", preview = "", error = "";
  let clearHeaders = false, clearSigningKey = false, saving = false;
  let bodyModes: string[] = [], jsonDrafts: string[] = [];
  let historyOffset = 0, historyMore = false, olderRuns: HookState["runs"] = [];
  let olderCalls: HookState["calls"] = [];
  let drop: {resource: "rules" | "destinations"; id: number; name: string} | undefined;
  $: currentCalls = [...hooks.calls, ...olderCalls.filter(row => !hooks.calls.some(item => item.id === row.id))];
  $: currentRuns = [...hooks.runs, ...olderRuns.filter(row => !hooks.runs.some(item => item.id === row.id))];

  function switchTab(next: typeof tab) {
    tab = next;
    error = "";
    historyOffset = 0;
    historyMore = true;
    olderCalls = [];
    olderRuns = [];
  }

  function editRule(existing?: Rule) {
    rule = existing ? structuredClone(existing) : {
      name: "", trigger: "sms.received", enabled: true, modem: null, match: "all",
      conditions: [], actions: [{type: "sms", template: "{{text}}"}]
    };
    bodyModes = rule.actions.map(action => action.jsonBody ? "jsonBody" : action.textBody != null ?
      "textBody" : action.fields ? "fields" : "default");
    jsonDrafts = rule.actions.map(action => JSON.stringify(action.jsonBody ?? {}, null, 2));
    preview = "";
    previewId = "";
    error = "";
  }

  function editDestination(existing?: Destination) {
    destination = existing ? structuredClone(existing) : {
      name: "", url: "", method: "POST", enabled: true, privateAddress: null
    };
    headers = "";
    signingKey = "";
    clearHeaders = false;
    clearSigningKey = false;
    error = "";
  }

  function newAction(type: Action["type"]): Action {
    if (type === "http") return {type, destination: hooks.destinations[0]?.id};
    if (type === "sms") return {type, template: rule?.trigger === "sms.received" ? "{{text}}" : ""};
    return {type};
  }

  function draftRule() {
    if (!rule) return;
    const {id, ...draft} = rule;
    return {...draft, modem: draft.modem || null, actions: draft.actions.map((action, index) => {
      if (action.type === "sms") return {
        ...action, recipient: action.recipient || null, modem: action.modem || null
      };
      if (action.type !== "http") return action;
      const mode = bodyModes[index];
      return {
        type: "http", destination: action.destination,
        fields: mode === "fields" ? action.fields : null,
        textBody: mode === "textBody" ? action.textBody ?? "" : null,
        jsonBody: mode === "jsonBody" ? JSON.parse(jsonDrafts[index]) : null
      };
    })};
  }

  async function perform(callback: () => Promise<void>) {
    if (saving) return;
    saving = true;
    error = "";
    try { await callback(); }
    catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    finally { saving = false; }
  }

  async function saveRule(event: SubmitEvent) {
    event.preventDefault();
    if (!rule) return;
    const id = rule.id;
    await perform(async () => {
      await request("/v1/hooks/rules" + (id ? "/" + id : ""), id ? "PUT" : "POST", draftRule());
      rule = undefined;
    });
  }

  async function saveDestination(event: SubmitEvent) {
    event.preventDefault();
    if (!destination) return;
    const {id, hasHeaders, hasSigningKey, ...draft} = destination;
    await perform(async () => {
      await request("/v1/hooks/destinations" + (id ? "/" + id : ""), id ? "PUT" : "POST", {
        ...draft, privateAddress: draft.privateAddress || null,
        headers: headers ? JSON.parse(headers) : clearHeaders ? {} : null,
        signingKey: signingKey || (clearSigningKey ? "" : null)
      });
      destination = undefined;
    });
  }

  async function testRule() {
    await perform(async () => {
      preview = "";
      if (!rule || !previewId) throw new Error("Select an SMS or call to preview.");
      const source = rule.trigger === "sms.received" ? {messageId: Number(previewId)} : {callId: previewId};
      const result = await request("/v1/hooks/preview", "POST", {rule: draftRule(), ...source});
      preview = result.matches ? "Matched\n" + JSON.stringify(result.actions, null, 2) : "No match";
    });
  }

  async function toggle(existing: Rule) {
    const {id, ...data} = existing;
    await perform(async () => {
      await request("/v1/hooks/rules/" + id, "PUT", {...data, enabled: !data.enabled});
    });
  }

  async function loadMore() {
    if (tab !== "calls" && tab !== "runs") return;
    const historyTab = tab;
    await perform(async () => {
      const offset = historyOffset || hooks[historyTab].length;
      const page = await request("/v1/hooks/" + historyTab + "?limit=50&offset=" + offset);
      if (tab !== historyTab) return;
      if (historyTab === "runs") olderRuns = [...olderRuns, ...page.items.filter(
        (row: HookState["runs"][number]) => !olderRuns.some(item => item.id === row.id)
      )];
      else olderCalls = [...olderCalls, ...page.items.filter(
        (row: HookState["calls"][number]) => !olderCalls.some(item => item.id === row.id)
      )];
      historyOffset = offset + page.items.length;
      historyMore = page.hasMore;
    });
  }
</script>

<section aria-label="Hooks">
  <div class="heading"><h2>Hooks</h2></div>
  <nav aria-label="Hooks screens">
    {#each ["rules", "destinations", "runs", "calls"] as item}
      <button class:active={tab === item} onclick={() => switchTab(item as typeof tab)}>
        {item === "runs" ? "Delivery history" : item === "calls" ? "Call history" :
          item === "destinations" ? "HTTP destinations" : "Rules"}
      </button>
    {/each}
  </nav>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if drop}
    <div class="row"><span>Delete {drop.name} and cancel pending actions?</span>
      <button onclick={() => perform(async () => {
        if (!drop) return;
        await request("/v1/hooks/" + drop.resource + "/" + drop.id, "DELETE");
        drop = undefined;
      })}>Confirm delete</button><button onclick={() => (drop = undefined)}>Cancel</button>
    </div>
  {/if}
  {#if tab === "rules"}
    {#if !rule}
      <button class="primary" onclick={() => editRule()}>New rule</button>
      {#each hooks.rules as item (item.id)}
        <article>
          <div class="heading"><strong>{item.name}</strong><span class="tag">{item.enabled ? "Enabled" : "Disabled"}</span></div>
          <p class="sub">{triggers[item.trigger]} · {item.actions.map(action => labels[action.type]).join(", ")}</p>
          <div class="row">
            <button onclick={() => editRule(item)}>Edit</button>
            <button onclick={() => toggle(item)}>{item.enabled ? "Disable" : "Enable"}</button>
            <button onclick={() => (drop = {resource: "rules", id: item.id!, name: item.name})}>Delete</button>
          </div>
        </article>
      {:else}<p class="sub">No rules yet.</p>{/each}
    {:else}
      <form onsubmit={saveRule}>
        <div class="row">
          <label>Rule name<input required maxlength="100" bind:value={rule.name}></label>
          <label class="check"><input type="checkbox" bind:checked={rule.enabled}> Enabled</label>
        </div>
        <fieldset><legend>When</legend><div class="row">
          <label>Trigger<select bind:value={rule.trigger} onchange={() => {previewId = ""; preview = "";}}>
            {#each Object.entries(triggers) as [value, label]}<option {value}>{label}</option>{/each}
          </select></label>
          <label>Receiving modem<select bind:value={rule.modem}>
            <option value={null}>Any modem</option>
            {#if rule.modem && !modems.some(item => item.id === rule?.modem)}<option value={rule.modem}>{rule.modem}</option>{/if}
            {#each modems as modem}<option value={modem.id}>{modem.model || modem.id}</option>{/each}
          </select></label>
        </div></fieldset>
        <fieldset><legend>If</legend>
          <label>Match<select bind:value={rule.match}><option value="all">All conditions</option><option value="any">Any condition</option></select></label>
          {#each rule.conditions as condition, index}
            <div class="row item">
              <label>Field<select bind:value={condition.field}>
                <option value="peer">Sender or caller</option><option value="text">Message text</option>
                <option value="callerId">Caller ID availability</option><option value="outcome">Call outcome</option>
              </select></label>
              <label>Operator<select bind:value={condition.op}>
                <option value="equals">Equals</option><option value="contains">Contains</option>
                <option value="startsWith">Starts with</option><option value="in">In list</option>
              </select></label>
              <label>{condition.op === "in" ? "Values, comma separated" : "Value"}
                <input required value={condition.op === "in" ? condition.values.join(",") : condition.values[0] ?? ""}
                  oninput={event => (condition.values = condition.op === "in" ?
                    event.currentTarget.value.split(",").map(value => value.trim()) : [event.currentTarget.value])}>
              </label>
              <label class="check"><input type="checkbox" bind:checked={condition.caseSensitive}> Case sensitive</label>
              <button type="button" onclick={() => (rule!.conditions = rule!.conditions.filter((_, i) => i !== index))}>Remove condition</button>
            </div>
          {/each}
          <button type="button" onclick={() => (rule!.conditions = [...rule!.conditions,
            {field: "peer", op: "equals", values: [""], caseSensitive: false}])}>Add condition</button>
        </fieldset>
        <fieldset><legend>Do</legend>
          {#each rule.actions as action, index}
            <div class="item">
              <div class="row">
                <label>Action<select value={action.type} onchange={event => {
                  rule!.actions[index] = newAction(event.currentTarget.value as Action["type"]);
                  bodyModes[index] = "default";
                  jsonDrafts[index] = "{}";
                }}>
                  {#each Object.entries(labels) as [value, label]}<option {value}>{label}</option>{/each}
                </select></label>
                <button type="button" disabled={rule.actions.length < 2} onclick={() => {
                  rule!.actions = rule!.actions.filter((_, i) => i !== index);
                  bodyModes = bodyModes.filter((_, i) => i !== index);
                  jsonDrafts = jsonDrafts.filter((_, i) => i !== index);
                }}>Remove action</button>
              </div>
              {#if action.type === "sms"}
                <div class="row">
                  <label>Recipient<input placeholder="Reply to sender" bind:value={action.recipient}></label>
                  <label>Send from<select bind:value={action.modem}>
                    <option value={undefined}>Receiving modem</option><option value={null}>Receiving modem (default)</option>
                    {#if action.modem && !modems.some(modem => modem.id === action.modem)}<option value={action.modem}>{action.modem}</option>{/if}
                    {#each modems as modem}<option value={modem.id}>{modem.model || modem.id}</option>{/each}
                  </select></label>
                </div>
                <label>SMS template<textarea required bind:value={action.template}></textarea></label>
              {:else if action.type === "http"}
                <div class="row">
                  <label>Destination<select required bind:value={action.destination}>
                    {#each hooks.destinations as item}<option value={item.id}>{item.name}{item.enabled ? "" : " (disabled)"}</option>{/each}
                  </select></label>
                  <label>Request body<select bind:value={bodyModes[index]}>
                    <option value="default">Standard event</option><option value="fields">Selected fields</option>
                    <option value="jsonBody">Custom JSON</option><option value="textBody">Plain text</option>
                  </select></label>
                </div>
                {#if bodyModes[index] === "fields"}
                  <label>Fields<select multiple bind:value={action.fields}>
                    {#each fields as value}<option {value}>{value}</option>{/each}
                  </select></label>
                {:else if bodyModes[index] === "jsonBody"}
                  <label>JSON with string values<textarea bind:value={jsonDrafts[index]}></textarea></label>
                {:else if bodyModes[index] === "textBody"}
                  <label>Body template<textarea bind:value={action.textBody}></textarea></label>
                {/if}
              {/if}
            </div>
          {/each}
          <button type="button" disabled={rule.actions.length >= 8} onclick={() => {
            rule!.actions = [...rule!.actions, newAction("sms")];
            bodyModes = [...bodyModes, "default"];
            jsonDrafts = [...jsonDrafts, "{}"];
          }}>Add action</button>
          <p class="sub">Templates accept <code>{"{{peer}}"}</code>, <code>{"{{text}}"}</code>,
            <code>{"{{modem}}"}</code>, and <code>{"{{outcome}}"}</code>.</p>
        </fieldset>
        <div class="row">
          <button class="primary" disabled={saving}>Save rule</button>
          <button type="button" onclick={() => (rule = undefined)}>Cancel</button>
        </div>
        <div class="row">
          <label>Preview record<select bind:value={previewId}>
            <option value="">Select a record</option>
            {#if rule.trigger === "sms.received"}
              {#each messages.filter(message => message.direction === "incoming") as message}
                <option value={String(message.id)}>{message.peer} · {message.body.slice(0, 45)}</option>
              {/each}
            {:else}
              {#each hooks.calls as call}
                <option value={call.id}>{call.peer || call.callerId} · {new Date(call.time * 1000).toLocaleString()}</option>
              {/each}
            {/if}
          </select></label>
          <button type="button" disabled={saving || !previewId} onclick={testRule}>Dry run</button>
        </div>
        {#if preview}<pre aria-live="polite">{preview}</pre>{/if}
      </form>
    {/if}
  {:else if tab === "destinations"}
    {#if !destination}
      <button class="primary" onclick={() => editDestination()}>New destination</button>
      {#each hooks.destinations as item (item.id)}
        <article><strong>{item.name}</strong><p class="sub">{item.method} · {item.enabled ? "Enabled" : "Disabled"}</p>
          <div class="row"><button onclick={() => editDestination(item)}>Edit</button>
            <button onclick={() => (drop = {resource: "destinations", id: item.id!, name: item.name})}>Delete</button></div>
        </article>
      {:else}<p class="sub">No destinations yet.</p>{/each}
    {:else}
      <form onsubmit={saveDestination}>
        <label>Destination name<input required maxlength="100" bind:value={destination.name}></label>
        <label>URL<input type="url" required bind:value={destination.url}></label>
        <div class="row">
          <label>Method<select bind:value={destination.method}>
            {#each ["POST", "GET", "HEAD", "PUT", "PATCH", "DELETE"] as method}<option>{method}</option>{/each}
          </select></label>
          <label>Approved private IP<input placeholder="Optional" bind:value={destination.privateAddress}></label>
          <label class="check"><input type="checkbox" bind:checked={destination.enabled}> Enabled</label>
        </div>
        <label>HTTP headers as JSON<textarea placeholder={destination.hasHeaders ? "Saved. Blank keeps current headers." : "{}"} bind:value={headers}></textarea></label>
        {#if destination.hasHeaders}<label class="check"><input type="checkbox" bind:checked={clearHeaders}> Clear saved headers</label>{/if}
        <label>Signing key<input type="password" autocomplete="new-password" bind:value={signingKey} placeholder={destination.hasSigningKey ? "Saved" : "Optional"}></label>
        {#if destination.hasSigningKey}<label class="check"><input type="checkbox" bind:checked={clearSigningKey}> Clear signing key</label>{/if}
        <div class="row"><button class="primary" disabled={saving}>Save destination</button>
          <button type="button" onclick={() => (destination = undefined)}>Cancel</button></div>
      </form>
    {/if}
  {:else if tab === "runs"}
    {#each currentRuns as item (item.id)}
      <article>
        <div class="heading"><strong>{item.name} · {labels[item.action as keyof typeof labels]}</strong><span class="tag">{item.status}</span></div>
        <p class="sub">{new Date(item.created * 1000).toLocaleString()} · {item.attempts} attempts
          {item.result?.httpStatus ? " · HTTP " + item.result.httpStatus : ""}{item.error ? " · " + item.error : ""}</p>
      </article>
    {:else}<p class="sub">No deliveries yet.</p>{/each}
    {#if historyMore && currentRuns.length >= 50}<button onclick={loadMore}>Load older deliveries</button>{/if}
  {:else}
    {#each currentCalls as item (item.id)}
      <article>
        <div class="heading"><strong>{item.peer || item.callerId}</strong><span class="tag">{item.outcome || item.state}</span></div>
        <p class="sub">{new Date(item.time * 1000).toLocaleString()} · {item.modem}</p>
      </article>
    {:else}<p class="sub">No incoming calls recorded yet.</p>{/each}
    {#if historyMore && currentCalls.length >= 50}<button onclick={loadMore}>Load older calls</button>{/if}
  {/if}
</section>

<style>
  section{margin-top:1rem;background:#181e1c;border:1px solid #353e3a;border-radius:.7rem;padding:1rem;min-width:0}
  h2{margin:0 0 .65rem;font-size:1.05rem}
  .heading,.row,nav{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center}
  .heading{justify-content:space-between}
  .row{align-items:end}
  nav{margin-bottom:1rem}
  form,fieldset{display:grid;gap:.65rem}
  fieldset{border:1px solid #353e3a;border-radius:.45rem;margin:0;padding:.65rem;min-width:0}
  fieldset > button{justify-self:start}
  legend{padding:0 .35rem;color:#96debd;font-weight:650}
  label{display:grid;gap:.2rem;font-size:.85rem;color:#b3c2ba;min-width:0}
  .check{display:flex;align-items:center;min-height:2.5rem}
  .item{padding:.65rem 0;border-top:1px solid #353e3a;display:grid;gap:.5rem}
  .row.item{display:flex}
  input,button,textarea,select{font:inherit;color:#e8eeea;background:#111614;border:1px solid #46554e;border-radius:.35rem;padding:.42rem .6rem;max-width:100%}
  button{cursor:pointer}
  button:disabled{cursor:default;opacity:.5}
  button:not(:disabled):hover,.active{border-color:#7af5ac}
  .primary{background:#7af5ac;color:#13251a;border-color:#7af5ac;font-weight:700}
  article{overflow-wrap:anywhere;border-bottom:1px solid #353e3a;padding:.7rem 0}
  article p,.sub{color:#9baca4;margin:.25rem 0;font-size:.85rem}
  .tag{font-size:.75rem;color:#96debd;background:#22342c;border-radius:.25rem;padding:.12rem .4rem}
  .error{color:#ffaaa5}
  pre{max-height:15rem;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#111614;padding:.7rem;border-radius:.35rem}
</style>

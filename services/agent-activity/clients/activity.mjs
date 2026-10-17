// Explicit Node 20+ contributor transport. Keep credentials in the server environment.
// Persist an immutable event before calling send(); the Python SDK includes an outbox.
export class AgentActivityClient {
  constructor(endpoint, credential) {
    const url = new URL(endpoint);
    if ((url.protocol !== "https:" && !(url.protocol === "http:" && ["127.0.0.1", "localhost"].includes(url.hostname))) || url.username || url.password || url.search || url.hash || !credential) throw new Error("HTTPS endpoint and enrolled credential required");
    this.endpoint = endpoint.replace(/\/$/, ""); this.credential = credential;
  }
  async request(method, path, data) {
    if (!["/v1/events", "/v1/identity-claims", "/v1/me"].includes(path)) throw new Error("Unsupported operation");
    const body = data === undefined ? undefined : JSON.stringify(data);
    if (body && Buffer.byteLength(body) > 65536) throw new Error("Payload exceeds contract");
    const response = await fetch(this.endpoint + path, {method, redirect:"error", signal:AbortSignal.timeout(20000), headers:{Authorization:`Bearer ${this.credential}`,"Content-Type":"application/json"}, body});
    if (!response.ok) throw new Error(`Unconfirmed delivery: HTTP ${response.status}`);
    const reader=response.body.getReader();let size=0;const chunks=[];
    for (;;) { const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>2000000){await reader.cancel();throw new Error("Response too large");}chunks.push(value); }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  }
  send(event) { return this.request("POST", "/v1/events", event); }
  claim(identity) { return this.request("POST", "/v1/identity-claims", identity); }
  report() { return this.request("GET", "/v1/me"); }
  withdraw() { return this.request("DELETE", "/v1/me"); }
}

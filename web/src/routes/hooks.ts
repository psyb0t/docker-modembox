export type Trigger = "sms.received" | "call.incoming" | "call.ended";
export type Condition = {field: string; op: string; values: string[]; caseSensitive: boolean};
export type Action = {
  type: "sms" | "http" | "decline";
  recipient?: string | null;
  modem?: string | null;
  template?: string;
  destination?: number;
  fields?: string[] | null;
  jsonBody?: Record<string, string> | null;
  textBody?: string | null;
};
export type Rule = {
  id?: number; name: string; enabled: boolean; trigger: Trigger; modem: string | null;
  match: "all" | "any"; conditions: Condition[]; actions: Action[];
};
export type Destination = {
  id?: number; name: string; enabled: boolean; url: string; method: string;
  privateAddress: string | null; hasHeaders?: boolean; hasSigningKey?: boolean;
};
export type Run = {
  id: number; name: string; action: string; status: string; attempts: number; error?: string;
  created: number; result?: {httpStatus?: number; messageId?: number; outcome?: string};
};
export type CallRecord = {
  id: string; modem: string; peer: string; callerId: string; time: number; state: string; outcome?: string;
};
export type HookState = {rules: Rule[]; destinations: Destination[]; runs: Run[]; calls: CallRecord[]};
export const emptyHooks: HookState = {rules: [], destinations: [], runs: [], calls: []};

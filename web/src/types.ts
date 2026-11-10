// Shapes of the JSON the API returns. Hand-written for now (a generator from the OpenAPI schema
// is planned); keep in sync with api/routes/*.py.

export type Role = "admin" | "viewer";
export type ItemStatus = "pending" | "queued" | "running" | "done" | "failed" | "skipped";

export interface Me { id: number; email: string; login: string; name: string; role: Role }

export interface RunConfig { ocr_enabled: boolean; anonymize: boolean }

export interface StageUse { prompt: string; model: string; ok: boolean; problem?: string }

export interface Run {
  id: number; name: string; status: string; note: string; created_at: string;
  config: RunConfig;
  teams?: number;
  source?: { kind?: string; prefix?: string; batches?: string[] } | null;
  uses?: { detect_enabled: boolean; grade: StageUse; detect: StageUse };
  stages?: Record<string, Partial<Record<ItemStatus, number>>>;
}

export interface StageState { status: ItemStatus; error?: string | null; cache_hit?: boolean; attempts?: number; duration_ms?: number | null }

export interface TeamRow {
  id: number; name: string; filename: string; is_pdf: boolean; csv_matched: boolean | null;
  precheck_status: "PASS" | "FAIL" | null; precheck_code: string | null;
  chars_with_spaces: number | null; slide_count: number | null;
  over_char_limit: boolean; char_limit: number;
  grade_total: number | null; grade_status: string | null; ai_probability: number | null;
  review_flags: string[];
  stages: Record<string, StageState>;
}

export interface TeamDetail {
  id: number; name: string; filename: string; is_pdf: boolean; extra_files: string[];
  csv_matched: boolean | null; artifacts: string[]; stages: Record<string, StageState>;
}

export interface UserRow { id: number; email: string; login: string; name: string; role: Role; active: boolean; last_login: string | null }

export interface Precheck { status: "PASS" | "FAIL"; code: string | null; reason: string | null; warnings: string[] }
export interface PageText { number: number; text: string; chars: number; source: string; has_images: boolean; empty: boolean }

export interface PromptRow { id: number; name: string; kind: string; version: number; hash: string; status: "draft" | "published"; note: string; chars: number; est_tokens: number; created_at: string; text?: string }
export interface ModelRow { id: string; provider: string; model: string; label: string; price_in_per_1m: number; price_out_per_1m: number; price_cached_per_1m: number; max_concurrency: number; reasoning_effort: string; enabled: boolean; prices_checked: string }
export interface CostInfo { spent_rub: number; cap_rub: number; calls: number; cache_hits: number; breakdown: { stage: string; model: string; cache_hit: boolean; calls: number; cost_rub: number; prompt_tokens: number; completion_tokens: number }[] }
export interface Estimate { teams_total: number; teams_eligible: number; total_rub: number; cap_rub: number; note: string; lines: { stage: string; variant: string; teams: number; tokens_in: number; tokens_out: number; cost_rub: number }[] }
export interface LlmCallRow { id: number; stage: string; variant: string; model: string; prompt: string; prompt_tokens: number; completion_tokens: number; cost_rub: number; latency_ms: number; cache_hit: boolean; status: string; error: string | null; at: string }

export interface EvidenceItem { slide: number | null; description: string }
export interface CriterionScore { name: string; score: number; evidence: EvidenceItem[]; feedback: string }
export interface GradeResult {
  team_name: string; status: "EVALUATED" | "DISQUALIFIED"; disqualification_code: string | null; disqualification_reason: string | null;
  scores: Record<string, CriterionScore> | null; sum_score_60: number; total_score_100: number; general_feedback: string;
  technical_warnings: string[]; needs_human_review: boolean; quality_flags: string[];
}
export interface GradeArtifact { result: GradeResult; meta: { variant: string; prompt: string; model: string; prompt_tokens: number; completion_tokens: number; cost_rub: number; attempts: number; llm_skipped?: boolean } }
export interface DetectArtifact { skipped?: boolean; reason?: string; result?: { ai_probability: number; verdict: string; confidence: string; rationale: string; signals: Record<string, { score: number; evidence: string }>; manipulation_attempt?: { found: boolean; slides: number[]; evidence: string } | null }; meta: { prompt: string; model: string; cost_rub: number } }
export interface PlaygroundOut { prompt: string; model: string; team: string; input_full: string; raw: string; parsed: unknown; error: string | null; prompt_tokens: number; completion_tokens: number; cost_rub: number; latency_ms: number; cache_hit: boolean; input_preview: string }

export interface IncomingBatch { batch: string; files: number; bytes: number; zips: string[]; csvs: string[]; unzipped_submissions: number }
export interface ExportInfo {
  bucket: string; prefix: string; files: { path: string; bytes: number }[];
  last_export: null | { exported_at: string; teams_exported: number; teams_missing_grade: string[]; worksheet: null | { rows: number; updated: number; delimiter: string };
                        tie_groups: Record<string, number>; needs_review: string[] };
}

export interface SettingsData {
  defaults: { grade_model: string; detect_model: string; grade_prompt: string; detect_prompt: string };
  export: { csv_mode: "auto" | "comma" | "tab" | "both"; score_line: string; feedback_header: string };
  stages: { detect_enabled: boolean };
  budget: { run_cap_rub: number };
  ocr: { max_pages: number; price_per_page_rub: number };
  processing: { max_upload_mb: number };
  prompt_names: string[];
}

export interface Bin { from: number; to: number; count: number; teams: string[]; open_end?: boolean }
export interface TopRow { id: number; name: string; grade: number | null; chars: number | null; ai: number | null }
export interface Analytics {
  runs: { id: number; name: string; created_at: string }[];
  run: { id: number; name: string; status: string } | null;
  kpi?: { teams: number; members: number | null; graded: number; disqualified: number; not_graded: number; avg_grade: number | null; median_grade: number | null; spent_rub: number; cost_per_work_rub: number | null };
  grade_hist?: Bin[]; length_hist?: Bin[]; char_limit?: number; over_limit?: number; top?: TopRow[]; bottom?: TopRow[];
}
export interface TeamFile { path: string; bytes: number }

export interface CacheInfo { llm: { objects: number; bytes: number }; ocr: { objects: number; bytes: number } }

// Worker -> SSE while stage 1 (ingest) runs: flat counters from core/stages/ingest.py IngestProgress.
export interface IngestEvent {
  percent: number | null;
  bytes_done: number; bytes_total: number;   // phase 1: downloading the source zip(s)
  unpack_done: number; unpack_total: number; // phase 2: extracting files into the canonical tree
  register_done: number; register_total: number; // phase 3: registering teams (hash + manifest)
}

// Worker -> SSE while a run is being deleted: one counter per stage (files in each bucket, then DB rows).
export type DeleteStage = "input" | "work" | "results" | "db";
export interface DeleteEvent { type: "delete"; stage?: DeleteStage; done?: number; total?: number; status?: "done" | "error"; error?: string }
export interface DeleteProgress { stages: Partial<Record<DeleteStage, { done: number; total: number }>>; error?: string | null }

export interface PlaygroundOptions {
  prompts: { id: number; name: string; version: number; status: string; est_tokens: number }[];
  models: { id: string; label: string }[];
  defaults: { grade_prompt: string; grade_model: string };
}

export interface ApiKeyRow { id: number; name: string; prefix: string; role: "viewer" | "admin"; created_at: string; expires_at: string | null; last_used_at: string | null; revoked_at: string | null }
export interface OpenApiDoc {
  info: { title: string; version: string };
  paths: Record<string, Record<string, OpenApiOp>>;
  components?: { schemas?: Record<string, SchemaObj> };
}
export interface SchemaObj { $ref?: string; type?: string; title?: string; description?: string; properties?: Record<string, SchemaObj>; required?: string[];
  items?: SchemaObj; anyOf?: SchemaObj[]; enum?: unknown[]; default?: unknown; format?: string; minLength?: number; maximum?: number; minimum?: number; pattern?: string }
export interface OpenApiOp {
  tags?: string[]; summary?: string; description?: string; "x-role": string;
  parameters?: { name: string; in: string; required?: boolean; schema?: SchemaObj; description?: string }[];
  requestBody?: { required?: boolean; content: Record<string, { schema: SchemaObj }> };
  responses?: Record<string, { description: string }>;
}

export interface BatchInfo {
  name: string; status: "reserved" | "uploaded" | "used" | "unregistered"; origin: "ui" | "cli" | "outside"; registered: boolean; created_at: string | null;
  files: number; bytes: number; zips: string[]; csvs: string[]; unzipped_submissions: number; problems: string[]; modified_after_use: boolean; usable: boolean; used_by: number[];
}
export interface BatchList { batches: BatchInfo[]; name_rule: string }
export interface Reserved { name: string; prefix: string; commands: string[]; commands_unzipped: string }

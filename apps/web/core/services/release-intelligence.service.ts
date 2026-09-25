import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type AppConnection = { origin: string; current_version: string; enabled: boolean };
export type ReviewPage = { id: string; name: string; updated_at: string };
export type ReviewCapabilities = {
  ai_configured: boolean;
  ai_error: string;
  browser_available: boolean;
  browser_message: string;
};
export type ReviewEvidence = {
  id: string;
  title: string;
  url: string;
  revision: string;
  captured_at: string;
  capture_mode?: string;
  app_version?: string;
  limitations?: string;
  screenshot?: string;
  evidence_status?: string;
};
export type PageReview = {
  id: string;
  status: string;
  error: string;
  created_at: string;
  evidence: ReviewEvidence[];
  results: { text?: string; model?: string };
};
export type ReviewRequest = {
  page_ids: string[];
  paths: string[];
  instructions: string;
  capture_mode: "http_only" | "browser_rendered";
};

export class ReleaseIntelligenceService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }
  private url(slug: string, project: string, path: string) {
    return `/api/workspaces/${encodeURIComponent(slug)}/projects/${project}/${path}/`;
  }
  async connection(slug: string, project: string): Promise<AppConnection | null> {
    return (await this.get(this.url(slug, project, "app-connection"))).data;
  }
  async saveConnection(slug: string, project: string, value: AppConnection): Promise<AppConnection> {
    return (await this.put(this.url(slug, project, "app-connection"), value)).data;
  }
  async pages(slug: string, project: string): Promise<ReviewPage[]> {
    return (await this.get(this.url(slug, project, "review-pages"))).data;
  }
  async capabilities(slug: string, project: string): Promise<ReviewCapabilities> {
    return (await this.get(this.url(slug, project, "review-capabilities"))).data;
  }
  async reviews(slug: string, project: string): Promise<PageReview[]> {
    return (await this.get(this.url(slug, project, "page-reviews"))).data;
  }
  async create(slug: string, project: string, value: ReviewRequest): Promise<PageReview> {
    return (await this.post(this.url(slug, project, "page-reviews"), value)).data;
  }
}

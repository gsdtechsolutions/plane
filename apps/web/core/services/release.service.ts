import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type ReleaseIssueOption = { id: string; name: string; identifier: string };
export type ProductRelease = {
  id: string;
  name: string;
  version: string;
  notes: string;
  status: "draft" | "published";
  published_at: string | null;
  app_version: string;
  github_release_id: string | null;
  pull_request_ids: string[];
  issues: ReleaseIssueOption[];
  sources: { id?: string; title?: string; url?: string; type?: string }[];
};
export type ReleaseInput = Pick<
  ProductRelease,
  "name" | "version" | "notes" | "app_version" | "github_release_id" | "pull_request_ids"
> & { issue_ids: string[] };
export type GithubDeliveryOptions = {
  pull_requests: { id: string; title: string; number: number; repository: string; state: string }[];
  releases: { id: string; name: string; tag_name: string; repository: string }[];
};
export class ReleaseService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }
  private path(slug: string, project: string) {
    return `/api/workspaces/${slug}/projects/${project}/releases/`;
  }
  async list(slug: string, project: string): Promise<{ releases: ProductRelease[]; public_anchor: string | null }> {
    return (await this.get(this.path(slug, project))).data;
  }
  async options(slug: string, project: string, search: string): Promise<{ issues: ReleaseIssueOption[] }> {
    return (await this.get(`${this.path(slug, project)}options/`, { params: { search } })).data;
  }
  async github(slug: string, project: string): Promise<GithubDeliveryOptions> {
    return (await this.get(`/api/workspaces/${slug}/projects/${project}/github-delivery/`)).data;
  }
  async save(slug: string, project: string, id: string | null, data: ReleaseInput): Promise<ProductRelease> {
    return (
      await (id ? this.patch(`${this.path(slug, project)}${id}/`, data) : this.post(this.path(slug, project), data))
    ).data;
  }
  async action(
    slug: string,
    project: string,
    id: string,
    action: "generate" | "publish" | "unpublish"
  ): Promise<ProductRelease> {
    return (await this.post(`${this.path(slug, project)}${id}/${action}/`, {})).data;
  }
}
export function releaseError(error: unknown): string {
  const data = (error as { response?: { data?: unknown } })?.response?.data;
  if (typeof data === "object" && data)
    return Object.entries(data)
      .map(
        ([key, value]) =>
          `${key === "error" ? "" : `${key}: `}${Array.isArray(value) ? value.join(" ") : String(value)}`
      )
      .join(" ");
  return "Unable to complete this request. Please try again.";
}

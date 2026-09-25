import { describe, expect, it } from "vitest";
import { EIssueLayoutTypes } from "@plane/types";
import { getComputedDisplayFilters } from "./base";

describe("default work item layout", () => {
  it("opens unconfigured views as status columns", () => {
    expect(getComputedDisplayFilters()).toMatchObject({ layout: "kanban", group_by: "state" });
    expect(getComputedDisplayFilters({ order_by: "-created_at" })).toMatchObject({
      layout: "kanban",
      group_by: "state",
      order_by: "-created_at",
    });
  });

  it("retains saved layouts and grouping", () => {
    for (const layout of Object.values(EIssueLayoutTypes)) {
      expect(getComputedDisplayFilters({ layout, group_by: "priority" })).toMatchObject({
        layout,
        group_by: "priority",
      });
    }
    expect(getComputedDisplayFilters({ layout: EIssueLayoutTypes.LIST, group_by: null })).toMatchObject({
      layout: "list",
      group_by: null,
    });
  });

  it("retains page-specific defaults", () => {
    expect(getComputedDisplayFilters({ sub_issue: true }, { layout: EIssueLayoutTypes.LIST })).toMatchObject({
      layout: "list",
      group_by: null,
      sub_issue: true,
    });
    expect(getComputedDisplayFilters({}, { layout: EIssueLayoutTypes.SPREADSHEET })).toMatchObject({
      layout: "spreadsheet",
      group_by: null,
    });
  });
});

/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect } from "react";
import { createPortal } from "react-dom";
import { observer } from "mobx-react";
import { ChevronLeftOutline, ChevronRightOutline, CloseOutline, DownloadOutline } from "@makeplane/propel/icons";
import type { TIssueServiceType } from "@plane/types";
import { EIssueServiceType } from "@plane/types";
import { cn, getFileExtension, getFileURL } from "@plane/utils";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";

// SVG is left out on purpose: the API serves it as a download because it can carry scripts.
const PREVIEWABLE_IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "avif"]);

export const isPreviewableImage = (fileName: string | undefined) =>
  PREVIEWABLE_IMAGE_EXTENSIONS.has(getFileExtension(fileName ?? "").toLowerCase());

type Props = {
  attachmentIds: string[];
  activeId: string | null;
  onChange: (attachmentId: string | null) => void;
  issueServiceType?: TIssueServiceType;
};

export const IssueAttachmentImagePreview = observer(function IssueAttachmentImagePreview(props: Props) {
  const { attachmentIds, activeId, onChange, issueServiceType = EIssueServiceType.ISSUES } = props;
  const {
    attachment: { getAttachmentById },
  } = useIssueDetail(issueServiceType);

  const index = activeId ? attachmentIds.indexOf(activeId) : -1;
  const attachment = activeId ? getAttachmentById(activeId) : undefined;
  const hasMultiple = attachmentIds.length > 1;

  const close = useCallback(() => onChange(null), [onChange]);
  const step = useCallback(
    (offset: number) => {
      if (index < 0 || !hasMultiple) return;
      onChange(attachmentIds[(index + offset + attachmentIds.length) % attachmentIds.length]);
    },
    [attachmentIds, hasMultiple, index, onChange]
  );

  useEffect(() => {
    if (!activeId) return;
    // Captured on window so Escape closes only the preview, not the peek overview behind it.
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowLeft") step(-1);
      else if (e.key === "ArrowRight") step(1);
      else return;
      e.preventDefault();
      e.stopImmediatePropagation();
    };
    window.addEventListener("keydown", handleKeyDown, true);
    return () => window.removeEventListener("keydown", handleKeyDown, true);
  }, [activeId, close, step]);

  if (!attachment || index < 0) return null;

  const fileName = attachment.attributes.name;
  const fileURL = getFileURL(attachment.asset_url) ?? "";
  const iconButtonClassName =
    "flex size-9 items-center justify-center rounded-full text-white/80 transition-colors hover:bg-white/15 hover:text-white";

  return createPortal(
    <div
      data-prevent-outside-click
      role="dialog"
      aria-modal="true"
      aria-label={fileName}
      className="fixed inset-0 z-[100] flex flex-col bg-black/85"
      onClick={close}
    >
      <div className="flex items-center justify-between gap-4 px-4 py-3 text-white" onClick={(e) => e.stopPropagation()}>
        <div className="flex min-w-0 items-center gap-3 text-13">
          <span className="truncate font-medium">{fileName}</span>
          {hasMultiple && (
            <span className="shrink-0 text-white/60">
              {index + 1} / {attachmentIds.length}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            className={iconButtonClassName}
            aria-label="Download"
            onClick={() => window.open(fileURL, "_blank")}
          >
            <DownloadOutline className="size-4" />
          </button>
          <button type="button" className={iconButtonClassName} aria-label="Close" onClick={close}>
            <CloseOutline className="size-5" />
          </button>
        </div>
      </div>

      <div className="relative flex min-h-0 flex-1 items-center justify-center px-16 pb-8">
        {hasMultiple && (
          <button
            type="button"
            className={cn(iconButtonClassName, "absolute left-4 size-11 bg-white/10")}
            aria-label="Previous image"
            onClick={(e) => {
              e.stopPropagation();
              step(-1);
            }}
          >
            <ChevronLeftOutline className="size-5" />
          </button>
        )}
        <img
          key={attachment.id}
          src={fileURL}
          alt={fileName}
          className="max-h-full max-w-full rounded-md object-contain shadow-2xl"
          onClick={(e) => e.stopPropagation()}
        />
        {hasMultiple && (
          <button
            type="button"
            className={cn(iconButtonClassName, "absolute right-4 size-11 bg-white/10")}
            aria-label="Next image"
            onClick={(e) => {
              e.stopPropagation();
              step(1);
            }}
          >
            <ChevronRightOutline className="size-5" />
          </button>
        )}
      </div>
    </div>,
    document.body
  );
});

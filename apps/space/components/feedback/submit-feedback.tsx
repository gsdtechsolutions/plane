/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Link } from "react-router";
import { usePathname } from "next/navigation";
import axios from "axios";
import { API_BASE_URL } from "@plane/constants";
import { Button } from "@makeplane/propel/components/button";
import {
  Dialog,
  DialogContent,
  DialogMain,
  DialogHeader,
  DialogTitle,
  DialogBody,
  DialogActions,
} from "@makeplane/propel/components/dialog";
import { useUser } from "@/hooks/store/use-user";

const escapeText = (text: string) =>
  text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

export const SubmitFeedback = observer(function SubmitFeedback({
  anchor,
  intakeId,
}: {
  anchor: string;
  intakeId: string;
}) {
  const { data: user } = useUser();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("bug");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (saving || !user || !title.trim()) return;
    setSaving(true);
    setError("");
    try {
      await axios.post(
        `${API_BASE_URL.replace(/\/$/, "")}/api/public/anchor/${anchor}/intakes/${intakeId}/intake-issues/`,
        {
          feedback_type: kind,
          issue: { name: title.trim(), description_html: `<p>${escapeText(description).replace(/\n/g, "<br>")}</p>` },
        },
        { withCredentials: true }
      );
      setSent(true);
      setTitle("");
      setDescription("");
    } catch (cause) {
      const status = axios.isAxiosError(cause) ? cause.response?.status : undefined;
      setError(
        status === 429
          ? "Too many submissions. Please try again later."
          : status === 401 || status === 403
            ? "Please sign in again before submitting feedback."
            : status === 404
              ? "This project is no longer accepting feedback."
              : "Your feedback was not sent. Please try again."
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Button
        variant="secondary"
        size="sm"
        stretch="auto"
        label="Submit feedback"
        onClick={() => {
          setSent(false);
          setError("");
          setOpen(true);
        }}
      />
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!saving) setOpen(next);
        }}
      >
        <DialogContent>
          <DialogMain>
            <DialogHeader>
              <DialogTitle>Submit feedback</DialogTitle>
            </DialogHeader>
            <DialogBody>
              {!user ? (
                <div className="space-y-3 text-14">
                  <p>Sign in to send a bug report or feature request to the team.</p>
                  <Link className="text-accent-primary underline" to={`/?next_path=${encodeURIComponent(pathname)}`}>
                    Sign in to submit
                  </Link>
                </div>
              ) : sent ? (
                <p role="status" className="text-14">
                  Thanks! Your feedback is waiting for the team to review it. It stays private until the team accepts it
                  for the public board.
                </p>
              ) : (
                <form id="public-feedback-form" onSubmit={submit} className="flex flex-col gap-4">
                  <p className="text-13 text-secondary">
                    Reports are private while the team reviews them. Do not include passwords or other sensitive
                    information.
                  </p>
                  <label className="flex flex-col gap-1 text-14">
                    Feedback type
                    <select
                      value={kind}
                      onChange={(event) => setKind(event.target.value)}
                      disabled={saving}
                      className="rounded-md border border-subtle bg-surface-1 p-2 text-primary"
                    >
                      <option value="bug">Bug report</option>
                      <option value="feature">Feature request</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1 text-14">
                    Title
                    <input
                      required
                      maxLength={255}
                      value={title}
                      disabled={saving}
                      onChange={(event) => setTitle(event.target.value)}
                      placeholder={kind === "bug" ? "What went wrong?" : "What would you like to do?"}
                      className="rounded-md border border-subtle bg-surface-1 p-2 text-primary"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-14">
                    Description
                    <textarea
                      maxLength={5000}
                      rows={5}
                      value={description}
                      disabled={saving}
                      onChange={(event) => setDescription(event.target.value)}
                      placeholder={
                        kind === "bug"
                          ? "Steps to reproduce, expected result, and what happened"
                          : "Describe the problem this feature would solve"
                      }
                      className="resize-y rounded-md border border-subtle bg-surface-1 p-2 text-primary"
                    />
                  </label>
                  {error && (
                    <p role="alert" className="text-13 text-danger-primary">
                      {error}
                    </p>
                  )}
                </form>
              )}
            </DialogBody>
            <DialogActions>
              <Button
                variant="secondary"
                size="sm"
                stretch="auto"
                label={sent ? "Done" : "Cancel"}
                disabled={saving}
                onClick={() => setOpen(false)}
              />
              {user && !sent && (
                <Button
                  type="submit"
                  form="public-feedback-form"
                  variant="primary"
                  size="sm"
                  stretch="auto"
                  label={saving ? "Sending…" : "Send feedback"}
                  disabled={saving || !title.trim()}
                />
              )}
            </DialogActions>
          </DialogMain>
        </DialogContent>
      </Dialog>
    </>
  );
});

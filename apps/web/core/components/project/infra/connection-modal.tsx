/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";
// plane imports
import { Field } from "@makeplane/propel/components/field";
import { Input } from "@makeplane/propel/components/input";
import { Button } from "@makeplane/propel/components/button";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogHeading,
  DialogMain,
  DialogTitle,
} from "@makeplane/propel/components/dialog";
import { Select } from "@plane/blocks/select";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
import type { IInfraConnection, TInfraService } from "@plane/types";
// services
import { InfraService } from "@/services/infra/infra.service";

const infraService = new InfraService();

const SERVICE_OPTIONS: { value: TInfraService; label: string }[] = [
  { value: "coolify", label: "Coolify" },
  { value: "grafana", label: "Grafana" },
];

const EMPTY_OPTION = { value: "", label: "" };

type TConnectionFormValues = {
  name: string;
  service: TInfraService;
  base_url: string;
  api_token: string;
};

const defaultValues: TConnectionFormValues = {
  name: "",
  service: "coolify",
  base_url: "",
  api_token: "",
};

export function ConnectionModal(props: {
  workspaceSlug: string;
  connection?: IInfraConnection;
  handleClose: () => void;
  onSaved: () => void;
}) {
  const { workspaceSlug, connection, handleClose, onSaved } = props;
  // states
  const [isSubmitting, setIsSubmitting] = useState(false);
  // hooks
  const { t } = useTranslation();

  const {
    control,
    formState: { errors },
    handleSubmit,
    reset,
  } = useForm<TConnectionFormValues>({ defaultValues });

  useEffect(() => {
    if (connection) {
      reset({
        name: connection.name,
        service: connection.service,
        base_url: connection.base_url,
        api_token: "",
      });
    } else {
      reset(defaultValues);
    }
    return () => reset(defaultValues);
  }, [connection, reset]);

  const handleFormSubmit = async (formData: TConnectionFormValues) => {
    setIsSubmitting(true);
    const payload: Record<string, unknown> = {
      name: formData.name.trim(),
      service: formData.service,
      base_url: formData.base_url.trim(),
    };
    if (formData.api_token) payload.api_token = formData.api_token;

    try {
      if (connection) {
        await infraService.updateConnection(workspaceSlug, connection.id, payload);
        setToast({
          type: "success",
          title: "Success!",
          message: t("project_settings.infra.connections.toasts.updated"),
        });
      } else {
        if (!formData.api_token) {
          setToast({
            type: "error",
            title: "Error!",
            message: t("project_settings.infra.connections.token_placeholder"),
          });
          setIsSubmitting(false);
          return;
        }
        await infraService.createConnection(workspaceSlug, { ...payload, api_token: formData.api_token });
        setToast({
          type: "success",
          title: "Success!",
          message: t("project_settings.infra.connections.toasts.created"),
        });
      }
      onSaved();
      handleClose();
    } catch (error) {
      const apiError = error as { base_url?: string[]; name?: string[]; error?: string };
      setToast({
        type: "error",
        title: "Error!",
        message: apiError?.base_url?.[0] ?? apiError?.error ?? t("project_settings.infra.connections.toasts.save_error"),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) handleClose();
      }}
    >
      <DialogContent size="md">
        <form onSubmit={handleSubmit(handleFormSubmit)} className="flex min-h-0 flex-1 flex-col">
          <DialogMain>
            <DialogHeader>
              <DialogHeading>
                <DialogTitle>
                  {connection
                    ? t("project_settings.infra.connections.heading")
                    : t("project_settings.infra.connections.add")}
                </DialogTitle>
              </DialogHeading>
            </DialogHeader>
            <DialogBody tabIndex={0}>
              <div className="space-y-3">
                <div>
                  <label htmlFor="infra-connection-name" className="mb-2 block text-14 font-medium text-secondary">
                    {t("project_settings.infra.connections.name")}
                  </label>
                  <Controller
                    control={control}
                    name="name"
                    rules={{ required: t("project_settings.infra.connections.name") }}
                    render={({ field: { value, onChange, ref } }) => (
                      <Field name="infra-connection-name" invalid={Boolean(errors.name)}>
                        <Input
                          size="2xl"
                          id="infra-connection-name"
                          type="text"
                          value={value}
                          onChange={onChange}
                          ref={ref}
                          placeholder={t("project_settings.infra.connections.name_placeholder")}
                        />
                      </Field>
                    )}
                  />
                  {errors.name && (
                    <span className="text-11 text-danger-primary">{t("project_settings.infra.connections.name")}</span>
                  )}
                </div>

                <div>
                  <label className="mb-2 block text-14 font-medium text-secondary">
                    {t("project_settings.infra.connections.service")}
                  </label>
                  <Controller
                    control={control}
                    name="service"
                    render={({ field: { value, onChange } }) => (
                      <Select
                        getValues={() => SERVICE_OPTIONS}
                        value={SERVICE_OPTIONS.find((option) => option.value === value) ?? EMPTY_OPTION}
                        onChange={(selected) => {
                          const selectedOption = SERVICE_OPTIONS.find((option) => option.value === selected);
                          if (selectedOption) onChange(selectedOption.value);
                        }}
                        getOptionValue={(option) => option.value}
                        getOptionLabel={(option) => option.label}
                        placeholder={t("project_settings.infra.connections.service")}
                        showSearch={false}
                        pinSelected={false}
                        contentSizing="anchor"
                      >
                        <Select.Trigger variant="select-md" className="w-full max-w-full">
                          <span className="min-w-0 grow truncate text-left">{value}</span>
                        </Select.Trigger>
                      </Select>
                    )}
                  />
                </div>

                <div>
                  <label htmlFor="infra-connection-base-url" className="mb-2 block text-14 font-medium text-secondary">
                    {t("project_settings.infra.connections.base_url")}
                  </label>
                  <Controller
                    control={control}
                    name="base_url"
                    rules={{ required: t("project_settings.infra.connections.base_url") }}
                    render={({ field: { value, onChange, ref } }) => (
                      <Field name="infra-connection-base-url" invalid={Boolean(errors.base_url)}>
                        <Input
                          size="2xl"
                          id="infra-connection-base-url"
                          type="text"
                          value={value}
                          onChange={onChange}
                          ref={ref}
                          placeholder={t("project_settings.infra.connections.base_url_placeholder")}
                        />
                      </Field>
                    )}
                  />
                  {errors.base_url && (
                    <span className="text-11 text-danger-primary">
                      {t("project_settings.infra.connections.base_url")}
                    </span>
                  )}
                </div>

                <div>
                  <label htmlFor="infra-connection-token" className="mb-2 block text-14 font-medium text-secondary">
                    {t("project_settings.infra.connections.token")}
                    <span className="block text-10">
                      {connection
                        ? t("project_settings.infra.connections.token_keep")
                        : t("project_settings.infra.connections.token_placeholder")}
                    </span>
                  </label>
                  <Controller
                    control={control}
                    name="api_token"
                    render={({ field: { value, onChange } }) => (
                      <Field name="infra-connection-token">
                        <Input
                          size="2xl"
                          id="infra-connection-token"
                          type="password"
                          value={value}
                          onChange={onChange}
                          placeholder={t("project_settings.infra.connections.token_placeholder")}
                        />
                      </Field>
                    )}
                  />
                </div>
              </div>
            </DialogBody>
          </DialogMain>
          <DialogActions>
            <Button
              variant="secondary"
              size="md"
              stretch="auto"
              onClick={handleClose}
              disabled={isSubmitting}
              label="Cancel"
            />
            <Button variant="primary" size="md" stretch="auto" type="submit" loading={isSubmitting} label="Save" />
          </DialogActions>
        </form>
      </DialogContent>
    </Dialog>
  );
}

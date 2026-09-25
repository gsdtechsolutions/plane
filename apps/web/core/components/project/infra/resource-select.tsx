/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { Select } from "@plane/blocks/select";

export type TResourceOption = { value: string; label: string };

const EMPTY_OPTION: TResourceOption = { value: "", label: "" };

export function ResourceSelect(props: {
  options: TResourceOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  const { options, value, onChange, placeholder } = props;
  const selected = options.find((option) => option.value === value) ?? EMPTY_OPTION;

  return (
    <Select<TResourceOption>
      getValues={() => options}
      value={selected}
      onChange={(selectedValue) => {
        const matched = options.find((option) => option.value === selectedValue);
        if (matched) onChange(matched.value);
      }}
      getOptionValue={(option) => option.value}
      getOptionLabel={(option) => option.label}
      placeholder={placeholder}
      showSearch={options.length > 8}
      pinSelected={false}
      contentSizing="anchor"
    >
      <Select.Trigger<TResourceOption> variant="select-md" className="w-full max-w-full">
        <span className="min-w-0 grow truncate text-left">{selected.value ? selected.label : placeholder}</span>
      </Select.Trigger>
    </Select>
  );
}

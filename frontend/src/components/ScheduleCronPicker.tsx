import { MultiSelect, SimpleGrid } from "@mantine/core";

export interface CronValue {
  minutes: number[];
  hours: number[];
  days: number[];
  weekdays: number[];
}

interface ScheduleCronPickerProps {
  value: CronValue;
  onChange: (value: CronValue) => void;
}

const WEEKDAY_LABELS = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];

function numOptions(from: number, to: number) {
  const opts: { value: string; label: string }[] = [];
  for (let i = from; i <= to; i++) opts.push({ value: String(i), label: String(i) });
  return opts;
}

const MINUTE_OPTIONS = numOptions(0, 59);
const HOUR_OPTIONS = numOptions(0, 23);
const DAY_OPTIONS = numOptions(1, 31);
const WEEKDAY_OPTIONS = WEEKDAY_LABELS.map((label, value) => ({ value: String(value), label }));

function toStrings(nums: number[]): string[] {
  return nums.map(String);
}

function toNumbers(strs: string[]): number[] {
  return strs.map(Number).sort((a, b) => a - b);
}

export function ScheduleCronPicker({ value, onChange }: ScheduleCronPickerProps) {
  return (
    <SimpleGrid cols={2}>
      <MultiSelect
        label="Minuten"
        placeholder="Pflichtfeld — mind. eine Minute"
        data={MINUTE_OPTIONS}
        value={toStrings(value.minutes)}
        onChange={(v) => onChange({ ...value, minutes: toNumbers(v) })}
        searchable
        required
      />
      <MultiSelect
        label="Stunden"
        placeholder="Leer = jede Stunde"
        data={HOUR_OPTIONS}
        value={toStrings(value.hours)}
        onChange={(v) => onChange({ ...value, hours: toNumbers(v) })}
        searchable
      />
      <MultiSelect
        label="Wochentage"
        placeholder="Leer = jeder Tag"
        data={WEEKDAY_OPTIONS}
        value={toStrings(value.weekdays)}
        onChange={(v) => onChange({ ...value, weekdays: toNumbers(v) })}
      />
      <MultiSelect
        label="Tage im Monat"
        placeholder="Leer = jeder Tag"
        data={DAY_OPTIONS}
        value={toStrings(value.days)}
        onChange={(v) => onChange({ ...value, days: toNumbers(v) })}
        searchable
      />
    </SimpleGrid>
  );
}

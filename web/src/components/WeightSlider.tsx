interface Props {
  skill: string;
  value: number;
  onChange: (v: number) => void;
}

/** 가중치는 API가 1~5로 제약한다(api/routes/optimize.py의 Field(ge=1, le=5)).
 *  UI에서 그 범위를 벗어날 수 없게 두어 422를 애초에 만들지 않는다. */
export function WeightSlider({ skill, value, onChange }: Props) {
  return (
    <div className="flex items-center gap-3 py-1">
      <label htmlFor={`w-${skill}`} className="w-32 shrink-0 truncate text-[13px] text-slate-700" title={skill}>
        {skill}
      </label>
      <input
        id={`w-${skill}`}
        aria-label={skill}
        type="range"
        min={1}
        max={5}
        step={1}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="min-w-0 flex-1 accent-[#15372F]"
      />
      <span className="w-6 text-right text-sm tabular-nums text-slate-900">{value}</span>
    </div>
  );
}

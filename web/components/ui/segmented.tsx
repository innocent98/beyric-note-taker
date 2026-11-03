// Radio buttons drawn as a segmented control. Works without JavaScript.
export function Segmented({
  name,
  options,
  defaultValue,
  legend,
}: {
  name: string;
  options: ReadonlyArray<readonly [string, string]>;
  defaultValue: string;
  legend: string;
}) {
  return (
    <fieldset className="segmented" aria-label={legend}>
      {options.map(([value, text]) => (
        <label className="segmented-opt" key={value}>
          <input type="radio" name={name} value={value} defaultChecked={value === defaultValue} />
          <span>{text}</span>
        </label>
      ))}
    </fieldset>
  );
}

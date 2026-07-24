"""Pure llama-bench argument-list construction from proven capability mappings."""

from __future__ import annotations

from pathlib import Path

from aarchtune.screening.errors import BenchCommandError
from aarchtune.screening.models import (
    BenchCommand,
    BenchSignature,
    BooleanOptionForm,
    LlamaBenchCapabilities,
    OutputFormat,
    ScenarioCommandForm,
    ScreeningScenario,
)


def _flag(capabilities: LlamaBenchCapabilities, name: str) -> str:
    mapping = capabilities.mappings[name]
    if not mapping.supported or mapping.selected_flag is None:
        raise BenchCommandError(f"Required llama-bench mapping is unavailable: {name}")
    return mapping.selected_flag


def build_scenario_arguments(
    capabilities: LlamaBenchCapabilities,
    scenario: ScreeningScenario,
) -> tuple[list[str], dict[str, str], ScenarioCommandForm]:
    if scenario.prompt_tokens and scenario.generation_tokens:
        flag = _flag(capabilities, "prompt_generation")
        return (
            [flag, f"{scenario.prompt_tokens},{scenario.generation_tokens}"],
            {"prompt_generation": flag},
            ScenarioCommandForm.COMBINED,
        )
    prompt_flag = _flag(capabilities, "prompt_tokens")
    generation_flag = _flag(capabilities, "generation_tokens")
    arguments = [
        prompt_flag,
        str(scenario.prompt_tokens),
        generation_flag,
        str(scenario.generation_tokens),
    ]
    mapped = {
        "prompt_tokens": prompt_flag,
        "generation_tokens": generation_flag,
    }
    form = ScenarioCommandForm.PREFILL if scenario.prompt_tokens else ScenarioCommandForm.DECODE
    return arguments, mapped, form


def build_bench_command(
    capabilities: LlamaBenchCapabilities,
    model_path: Path,
    signature: BenchSignature,
    scenario: ScreeningScenario,
    repetition: int,
) -> BenchCommand:
    if not signature.compatible:
        raise BenchCommandError(
            f"Signature {signature.id} is incompatible: "
            f"{', '.join(signature.incompatibility_reasons)}"
        )
    arguments = [str(capabilities.binary_path)]
    mapped: dict[str, str] = {}

    def add(name: str, value: object) -> None:
        selected = _flag(capabilities, name)
        arguments.extend([selected, str(value)])
        mapped[name] = selected

    add("model_path", model_path)
    values = signature.settings.model_dump(mode="python", exclude={"schema_version"})
    for name in ("threads", "threads_batch", "batch_size", "ubatch_size"):
        value = values[name]
        if value is not None:
            add(name, value)
    mmap = signature.settings.mmap
    if mmap is not None:
        mapping = capabilities.mappings["mmap"]
        if mapping.boolean_form is BooleanOptionForm.NUMERIC_01:
            arguments.extend(["--mmap", "1" if mmap else "0"])
            mapped["mmap"] = "--mmap"
        elif mapping.boolean_form is BooleanOptionForm.PAIRED_SWITCHES:
            selected = "--mmap" if mmap else "--no-mmap"
            arguments.append(selected)
            mapped["mmap"] = selected
        elif mmap and mapping.boolean_form is BooleanOptionForm.TRUE_ONLY:
            arguments.append("--mmap")
            mapped["mmap"] = "--mmap"
        else:
            form = mapping.boolean_form or BooleanOptionForm.UNSUPPORTED
            raise BenchCommandError(
                f"mmap={mmap} cannot be represented by llama-bench boolean form {form.value}"
            )
    if signature.settings.numa_mode not in {None, "disabled"}:
        add("numa_mode", signature.settings.numa_mode)
    scenario_arguments, scenario_mappings, scenario_form = build_scenario_arguments(
        capabilities, scenario
    )
    arguments.extend(scenario_arguments)
    mapped.update(scenario_mappings)
    if capabilities.mappings["repetitions"].supported:
        add("repetitions", 1)
    output_format = capabilities.output.selected_format
    output_flag = _flag(capabilities, "output_format")
    if output_flag in {"--jsonl", "--json", "--csv"}:
        arguments.append(output_flag)
    else:
        arguments.extend([output_flag, output_format.value])
    mapped["output_format"] = output_flag
    return BenchCommand(
        arguments=arguments,
        mapped_flags=mapped,
        output_format=OutputFormat(output_format),
        signature_id=signature.id,
        scenario_id=scenario.id,
        scenario_command_form=scenario_form,
        requested_prompt_tokens=scenario.prompt_tokens,
        requested_generation_tokens=scenario.generation_tokens,
        repetition=repetition,
    )

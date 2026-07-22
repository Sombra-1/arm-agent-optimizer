from aarchtune.hardware.features import arm_features_from_flags, normalize_cpu_flags


def test_normalize_cpu_flags_removes_blanks_case_and_duplicates() -> None:
    assert normalize_cpu_flags([" ASIMD ", "asimd", "", "I8MM"]) == {"asimd", "i8mm"}


def test_arm_feature_aliases_are_normalized() -> None:
    features = arm_features_from_flags(["neon", "asimddp", "i8mm", "sve2", "sme_f64f64"])

    assert features.asimd is True
    assert features.dotprod is True
    assert features.i8mm is True
    assert features.sve is True
    assert features.sme is True


def test_unrelated_flags_do_not_create_arm_capabilities() -> None:
    features = arm_features_from_flags(["avx2", "sse4_2"])

    assert not any(features.model_dump().values())

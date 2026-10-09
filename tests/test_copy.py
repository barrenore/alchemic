"""Copying a model instance and calling model_construct are refused."""

import copy

import pytest

from alchemic import Mapped, Model, mapped_column


class Relic(Model):
    __tablename__ = "copy_relics"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


@pytest.mark.parametrize(
    "operation",
    [
        copy.copy,
        copy.deepcopy,
        lambda instance: instance.model_copy(),
        lambda instance: instance.model_copy(deep=True),
        lambda instance: instance.model_copy(update={"name": "b"}),
        lambda instance: copy.replace(instance, name="b"),
    ],
    ids=["copy", "deepcopy", "model_copy", "model_copy_deep", "model_copy_update", "copy_replace"],
)
def test_copying_is_refused(operation):
    relic = Relic(id=1, name="a")
    with pytest.raises(TypeError, match=r"a Relic cannot be copied.*Relic\(\.\.\.\).*update\(\)"):
        operation(relic)
    assert relic.name == "a"
    relic.name = "still usable"
    assert relic.name == "still usable"


def test_model_construct_is_refused():
    with pytest.raises(TypeError, match=r"Relic\.model_construct\(\).*Relic\(\.\.\.\)"):
        Relic.model_construct(id=1, name="a")  # type: ignore[call-arg]

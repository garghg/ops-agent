from pydantic import ValidationError
import pytest
from src.schemas.template import TemplateConfig

def test_defaults():
    config = TemplateConfig()
    assert config.schedule.opening_hour == 10
    assert config.schedule.opening_min == 0
    assert config.schedule.closing_hour == 18
    assert config.schedule.closing_min == 0

def test_override():
    config = TemplateConfig(schedule={"opening_hour": 8})
    assert config.schedule.opening_hour == 8
    assert config.schedule.closing_hour == 18

def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        TemplateConfig(bogus="x")
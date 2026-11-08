"""Compatibility facade for the public research financial feature interface."""
from ..research.features import financial as _owner
from ..research.features.financial import (COMPANY_QUARTER_VERSION, TRANSFORMATION_VERSION, MLContractError, MLFeature, QuarterFeature, CompanyQuarterFeatures, build_screener_ml_features, company_quarter_features)

def __getattr__(name):
    return getattr(_owner, name)

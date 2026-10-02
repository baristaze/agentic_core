"""The budgets swimlane: what a call may spend and what it cost. Pricing,
its one source of list prices, is here; the resolver of the models swimlane
asks it for a row before a model can be picked."""

from .pricing import PricingInterface

__all__ = ["PricingInterface"]

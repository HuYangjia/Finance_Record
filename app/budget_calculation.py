"""Read-only inputs shared within one budget report's database snapshot.

Callers own the transaction. Discard this object after any write or transaction
boundary; never keep it on a connection, application state, or a global cache.
Consumers must not mutate the shared configuration, entries, or funding inputs.
"""
from functools import cached_property


class BudgetCalculation:
    def __init__(self, connection):
        self.connection = connection

    @cached_property
    def config(self):
        from app.budgets import config_data
        return config_data(self.connection)

    @cached_property
    def ledger(self):
        from app.personal_cashflow import read_ledger
        return read_ledger(self.connection)

    @property
    def rates(self):
        return self.ledger[2]

    @cached_property
    def entries(self):
        from app.personal_cashflow import build_entries
        return build_entries(*self.ledger)

    @cached_property
    def expenses(self):
        from app.budget_events import expense_entries
        return list(expense_entries(self.entries))

    @cached_property
    def funding(self):
        from app.budget_funding import FundingCatalog
        return FundingCatalog(self.connection, calculation=self)


def get_calculation(connection, calculation=None):
    if calculation is None:
        return BudgetCalculation(connection)
    if calculation.connection is not connection:
        raise ValueError('Budget calculations must use the same database connection.')
    return calculation

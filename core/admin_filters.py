from __future__ import annotations

from datetime import date, datetime, time

from django.contrib.admin.filters import FieldListFilter
from django.contrib.admin.options import IncorrectLookupParameters
from django.db import models
from django.utils import timezone


class DateRangeFieldListFilter(FieldListFilter):
    template = 'admin/date_range_filter.html'

    def __init__(self, field, request, params, model, model_admin, field_path):
        self.from_parameter_name = f'{field_path}__from'
        self.to_parameter_name = f'{field_path}__to'
        super().__init__(
            field,
            request,
            params,
            model,
            model_admin,
            field_path=field_path,
        )

        if not isinstance(field, models.DateField):
            raise TypeError(
                f'{self.__class__.__name__} requires a DateField/DateTimeField '
                f'(got {type(field)!r} for {field_path!r})'
            )

    def expected_parameters(self):
        return [self.from_parameter_name, self.to_parameter_name]

    def _get_parameter_value(self, parameter_name: str) -> str:
        value = self.used_parameters.get(parameter_name, '')
        if isinstance(value, list):
            value = value[-1] if value else ''
        if value is None:
            return ''
        if not isinstance(value, str):
            raise IncorrectLookupParameters(
                f'Invalid parameter type for {parameter_name!r}: {type(value)!r}'
            )
        return value

    @staticmethod
    def _parse_date(value: str) -> date:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise IncorrectLookupParameters(f'Invalid date: {value!r}') from exc

    @staticmethod
    def _start_of_day(value: date) -> datetime:
        dt = datetime.combine(value, time.min)
        return timezone.make_aware(dt, timezone.get_current_timezone())

    @staticmethod
    def _end_of_day(value: date) -> datetime:
        dt = datetime.combine(value, time.max)
        return timezone.make_aware(dt, timezone.get_current_timezone())

    def queryset(self, request, queryset):
        from_value = self._get_parameter_value(self.from_parameter_name)
        to_value = self._get_parameter_value(self.to_parameter_name)

        if not from_value and not to_value:
            return queryset

        from_date = self._parse_date(from_value) if from_value else None
        to_date = self._parse_date(to_value) if to_value else None

        if isinstance(self.field, models.DateTimeField):
            if from_date:
                queryset = queryset.filter(
                    **{f'{self.field_path}__gte': self._start_of_day(from_date)}
                )
            if to_date:
                queryset = queryset.filter(
                    **{f'{self.field_path}__lte': self._end_of_day(to_date)}
                )
            return queryset

        if from_date:
            queryset = queryset.filter(**{f'{self.field_path}__gte': from_date})
        if to_date:
            queryset = queryset.filter(**{f'{self.field_path}__lte': to_date})
        return queryset

    def choices(self, changelist):
        other_params = [
            (key, value)
            for key, value in changelist.params.items()
            if key not in self.expected_parameters() and key != 'p'
        ]

        yield {
            'from_parameter_name': self.from_parameter_name,
            'to_parameter_name': self.to_parameter_name,
            'from_value': self._get_parameter_value(self.from_parameter_name),
            'to_value': self._get_parameter_value(self.to_parameter_name),
            'other_params': other_params,
            'reset_query_string': changelist.get_query_string(
                remove=[*self.expected_parameters(), 'p']
            ),
        }

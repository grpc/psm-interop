# Copyright 2020 gRPC authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""This contains common retrying helpers (retryers).

We use tenacity as a general-purpose retrying library.

> It [tenacity] originates from a fork of retrying which is sadly no
> longer maintained. Tenacity isn’t api compatible with retrying but >
> adds significant new functionality and fixes a number of longstanding bugs.
> - https://tenacity.readthedocs.io/en/latest/index.html
"""
import datetime
import logging
from typing import Any, Callable, List, Optional, Tuple, Type

import tenacity
from tenacity import _utils as tenacity_utils
from tenacity import compat as tenacity_compat
from tenacity import stop
from tenacity import wait
from tenacity.retry import retry_base
from typing_extensions import Self

import framework.errors

retryers_logger = logging.getLogger(__name__)
# Type aliases
timedelta = datetime.timedelta
Retrying = tenacity.Retrying
CheckResultFn = Callable[[Any], bool]
_ExceptionClasses = Tuple[Type[Exception], ...]


def _build_retry_conditions(
    *,
    retry_on_exceptions: Optional[_ExceptionClasses] = None,
    check_result: Optional[CheckResultFn] = None,
) -> List[retry_base]:
    # Retry on all exceptions by default
    if retry_on_exceptions is None:
        retry_on_exceptions = (Exception,)

    retry_conditions = [tenacity.retry_if_exception_type(retry_on_exceptions)]
    if check_result is not None:
        if retry_on_exceptions:
            # When retry_on_exceptions is set, also catch them while executing
            # check_result callback.
            check_result = _safe_check_result(check_result, retry_on_exceptions)
        retry_conditions.append(tenacity.retry_if_not_result(check_result))
    return retry_conditions


def exponential_retryer_with_timeout(
    *,
    wait_min: timedelta,
    wait_max: timedelta,
    timeout: timedelta,
    retry_on_exceptions: Optional[_ExceptionClasses] = None,
    check_result: Optional[CheckResultFn] = None,
    logger: Optional[logging.Logger] = None,
    log_level: Optional[int] = logging.DEBUG,
    error_note: str = "",
) -> Retrying:
    if logger is None:
        logger = retryers_logger
    if log_level is None:
        log_level = logging.DEBUG

    retry_conditions = _build_retry_conditions(
        retry_on_exceptions=retry_on_exceptions, check_result=check_result
    )
    retry_error_callback = _on_error_callback(
        timeout=timeout, check_result=check_result, error_note=error_note
    )
    return Retrying(
        retry=tenacity.retry_any(*retry_conditions),
        wait=wait.wait_exponential(
            min=wait_min.total_seconds(), max=wait_max.total_seconds()
        ),
        stop=stop.stop_after_delay(timeout.total_seconds()),
        before_sleep=_before_sleep_log(logger, log_level),
        retry_error_callback=retry_error_callback,
    )


def constant_retryer(
    *,
    wait_fixed: timedelta,
    attempts: int = 0,
    timeout: Optional[timedelta] = None,
    retry_on_exceptions: Optional[_ExceptionClasses] = None,
    check_result: Optional[CheckResultFn] = None,
    logger: Optional[logging.Logger] = None,
    log_level: Optional[int] = logging.DEBUG,
    error_note: str = "",
) -> Retrying:
    if logger is None:
        logger = retryers_logger
    if log_level is None:
        log_level = logging.DEBUG
    if attempts < 1 and timeout is None:
        raise ValueError("The number of attempts or the timeout must be set")
    stops = []
    if attempts > 0:
        stops.append(stop.stop_after_attempt(attempts))
    if timeout is not None:
        stops.append(stop.stop_after_delay(timeout.total_seconds()))

    retry_conditions = _build_retry_conditions(
        retry_on_exceptions=retry_on_exceptions, check_result=check_result
    )
    retry_error_callback = _on_error_callback(
        timeout=timeout,
        attempts=attempts,
        check_result=check_result,
        error_note=error_note,
    )
    return Retrying(
        retry=tenacity.retry_any(*retry_conditions),
        wait=wait.wait_fixed(wait_fixed.total_seconds()),
        stop=stop.stop_any(*stops),
        before_sleep=_before_sleep_log(logger, log_level),
        retry_error_callback=retry_error_callback,
    )


def _on_error_callback(
    *,
    timeout: Optional[timedelta] = None,
    attempts: int = 0,
    check_result: Optional[CheckResultFn] = None,
    error_note: str = "",
):
    """A helper to propagate the initial state to the RetryError, so that
    it can assemble a helpful message containing timeout/number of attempts.
    """

    def error_handler(retry_state: tenacity.RetryCallState):
        raise RetryError(
            retry_state,
            timeout=timeout,
            attempts=attempts,
            check_result=check_result,
            note=error_note,
        )

    return error_handler


def _safe_check_result(
    check_result: CheckResultFn, retry_on_exceptions: _ExceptionClasses
) -> CheckResultFn:
    """Wraps check_result callback to catch and handle retry_on_exceptions.

    Normally tenacity doesn't retry when retry_if_result/retry_if_not_result
    raise an error. This wraps the callback to automatically catch Exceptions
    specified in the retry_on_exceptions argument.

    Ideally we should make all check_result callbacks to not throw, but
    in case it does, we'd rather be annoying in the logs, than break the test.
    """

    def _check_result_wrapped(result):
        try:
            return check_result(result)
        except retry_on_exceptions:
            retryers_logger.warning(
                (
                    "Result check callback %s raised an exception."
                    "This shouldn't happen, please handle any exceptions and "
                    "return return a boolean."
                ),
                tenacity_utils.get_callback_name(check_result),
                exc_info=True,
            )
            return False

    return _check_result_wrapped


def _before_sleep_log(logger, log_level, exc_info=False):
    """Same as tenacity.before_sleep_log, but only logs primitive return values.
    This is not useful when the return value is a dump of a large object.
    """

    def log_it(retry_state):
        if retry_state.outcome.failed:
            ex = retry_state.outcome.exception()
            verb, value = "raised", "%s: %s" % (type(ex).__name__, ex)

            if exc_info:
                local_exc_info = tenacity_compat.get_exc_info_from_future(
                    retry_state.outcome
                )
            else:
                local_exc_info = False
        else:
            local_exc_info = False  # exc_info does not apply when no exception
            result = retry_state.outcome.result()
            if isinstance(result, (int, bool, str)):
                verb, value = "returned", result
            else:
                verb, value = "returned type", type(result)

        logger.log(
            log_level,
            "Retrying %s in %s seconds as it %s %s",
            tenacity_utils.get_callback_name(retry_state.fn),
            getattr(retry_state.next_action, "sleep"),
            verb,
            value,
            exc_info=local_exc_info,
        )

    return log_it


class RetryError(tenacity.RetryError):
    # Note: framework.errors.FrameworkError could be used as a mixin,
    # but this would rely too much on tenacity.RetryError to not change.

    last_attempt: tenacity.Future
    note: str = ""
    _message: str = ""
    _print_last_trace: bool = True

    def __init__(
        self,
        retry_state,
        *,
        timeout: Optional[timedelta] = None,
        attempts: int = 0,
        check_result: Optional[CheckResultFn] = None,
        note: str = "",
        print_last_trace: bool = True,
    ):
        last_attempt: tenacity.Future = retry_state.outcome
        super().__init__(last_attempt)
        self._print_last_trace = print_last_trace

        message = f"Retry error"

        if retry_state.fn is None:
            # Context manager
            message += f":"
        else:
            callback_name = tenacity_utils.get_callback_name(retry_state.fn)
            message += f" calling {callback_name}:"

        if timeout:
            message += f" timeout {timeout} (h:mm:ss) exceeded"
            if attempts:
                message += " or"

        if attempts:
            message += f" {attempts} attempts exhausted"

        message += "."

        if last_attempt.failed:
            err = last_attempt.exception()
            message += f" Last Retry Attempt Error: {type(err).__name__}({err})"
        elif check_result:
            message += " Check result callback returned False."

        self._message = message

        if note:
            self.add_note(note)

    @property
    def message(self):
        # TODO(sergiitk): consider if we want to have print-by-default
        # and/or ignore-by-default exception lists.
        tb_out = ""
        if self._print_last_trace and (cause := self.exception()):
            try:
                if last_tb := framework.errors.format_error_with_trace(
                    cause, only_if_tb_present=True
                ):
                    tb_out = f"^\nLast Retry Attempt Error {last_tb.rstrip()}"
            except Exception as err_tb_format:
                tb_out = f"<Error printing the traceback: {err_tb_format!r}>"

        return f"{self._message}\n{tb_out}" if tb_out else self._message

    def result(self, *, default=None):
        return (
            self.last_attempt.result()
            if not self.last_attempt.failed
            else default
        )

    def exception(self, *, default=None):
        return (
            self.last_attempt.exception()
            if self.last_attempt.failed
            else default
        )

    def exception_str(self) -> str:
        return f"Error: {self._exception_str(self.exception())}"

    def result_str(self) -> str:
        result = self.result()
        return f"Result: {result}" if result is not None else "No result"

    def reason_str(self):
        return self.exception_str() if self.exception() else self.result_str()

    @classmethod
    def _exception_str(cls, err: Optional[BaseException]) -> str:
        return f"{type(err).__name__}: {err}" if err else "???"

    def with_last_trace(self, enabled: bool = True) -> Self:
        self._print_last_trace = enabled
        return self

    # TODO(sergiitk): Remove in py3.11, this will be built-in. See PEP 678.
    def add_note(self, note: str):
        self.note = note

    def __str__(self):
        return self.message if not self.note else f"{self.message}\n{self.note}"

"""Shared test fixtures."""

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

# Use a single event loop for all tests
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

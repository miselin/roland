#!/usr/bin/env python3

import imp
import pytest

from unittest.mock import MagicMock


@pytest.fixture
def roland():
    return imp.load_source('roland', 'bin/roland')


@pytest.fixture
def browser_commands():
    roro = roland()
    commands = roro.BrowserCommands()
    commands.roland = MagicMock()
    commands.webview = MagicMock()
    return commands


@pytest.fixture
def real_browser_commands():
    roro = roland()
    commands = roro.BrowserCommands()
    commands.webview = roro.WebKit.WebView()
    return commands


@pytest.fixture
def browser_window():
    roro = roland()
    return roro.BrowserWindow(roland=MagicMock())


@pytest.mark.parametrize('bytecount,expected_output', [
    (1000, '1000b'),
    (1024, '1kb'),
    (10240, '10kb'),
    (102400, '100kb'),
    (1024*1024, '1mb'),
    (1024*1024*512, '512mb'),
    (1024*1024*1024, '1gb'),
    (1024*1024*1024*512, '512gb'),
    (1024*1024*1024*1024, '1tb'),
])
def test_pretty_size(bytecount, expected_output, roland):
    assert roland.get_pretty_size(bytecount) == expected_output


class TestBrowserCommands:
    @pytest.mark.parametrize('url,new_window', [
        ('', False),
        (None, False),
        ('', True),
        (None, True),
        ('frozen brains tell no tales', True),
        ('frozen brains tell no tales', False),
    ])
    def test_open(self, url, new_window, browser_commands):
        if url is None:
            browser_commands.roland.prompt.return_value = 'cool search'

        browser_commands.open(url, new_window)

        if not url:
            assert browser_commands.roland.prompt.mock_calls
            url = browser_commands.roland.prompt.return_value

        if new_window:
            browser_commands.roland.new_window.assert_call(url)
        else:
            browser_commands.webview.load_uri.assert_any_call(url)

    @pytest.mark.parametrize('command', [
        'back',
        'forward',
        'move',
        'reload',
        'reload_bypass_cache',
        'stop',
        'zoom_in',
        'zoom_out',
        'zoom_reset',
    ])
    def test_real_commands_exist(self, command, real_browser_commands):
        command = getattr(real_browser_commands, command)
        command()


class TestBrowserWindow:
    @pytest.mark.parametrize('command,expected_exist', [
        ('cool_function', False),
        ('cool_function', True),
    ])
    def test_run_command(self, command, expected_exist, browser_window):
        if expected_exist:
            setattr(browser_window, command, MagicMock(side_effect=Exception('lol no')))

        browser_window.run_command(command)
        browser_window.roland.notify.assert_has_call("No such command '{}'".format(command))

        if expected_exist:
            browser_window.roland.notify.assert_has_call("Error calling '{}': {}'".format(command, 'lol no'))

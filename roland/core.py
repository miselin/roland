#!/usr/bin/env python3

import code
import contextlib
import enum
import html
import imp
import itertools
import json
import os
import re
import shlex
import socket
import sqlite3
import threading
import traceback

from urllib import parse as urlparse

from gi.repository import GLib, GObject, Gdk, Gio, Gtk, Notify, Soup, WebKit

Mode = enum.Enum('Mode', 'Insert Normal Motion SubCommand Command')


class BrowserCommands:
    def open(self, url=None, new_window=False):
        url = url or self.roland.prompt("URL:", options=self.roland.most_popular_urls())
        if new_window:
            self.roland.new_window(url)
        else:
            self.webview.load_uri(url)

    def save_session(self):
        self.roland.save_session()

    def open_or_search(self, text=None, new_window=False):
        text = text or self.roland.prompt("URL:", options=self.roland.most_popular_urls(), default_first=False)
        if urlparse.urlparse(text).scheme:
            self.open(text, new_window=new_window)
        else:
            if ' ' in text or '_' in text:
                self.search(text, new_window=new_window)
            else:
                try:
                    socket.gethostbyname(text)
                except socket.error:
                    self.search(text)
                else:
                    self.open('http://'+text, new_window=new_window)

    def navigate_up(self):
        url = self.webview.get_uri()
        url = urlparse.urlparse(url)
        if url.path not in ('', '/'):
            url = url._replace(path=os.path.dirname(url.path)).geturl()
            self.open(url)

    def navigate_top(self):
        url = self.webview.get_uri()
        url = urlparse.urlparse(url)._replace(path='').geturl()
        self.open(url)

    def close(self):
        # explicitly trigger quitting in case downloads are in progress
        if len(self.roland.get_windows()) == 1:
            self.roland.quit()
            return

        Gtk.Window.close(self)
        Gtk.Window.destroy(self)

    def change_user_agent(self, user_agent=None):
        self.roland.change_user_agent(user_agent=user_agent)

    def open_modify(self, new_window=False):
        url = self.roland.prompt("URL:", options=[self.webview.get_uri() or ''])
        self.open(url, new_window=new_window)

    def search(self, text=None, new_window=False):
        url = self.roland.config.search_page % urlparse.quote(text or self.roland.prompt('Search:'))
        self.open(url, new_window=new_window)

    def back(self):
        self.webview.go_back()

    def forward(self):
        self.webview.go_forward()

    def run_javascript(self, script):
        self.webview.execute_script(script)

    def follow(self, new_window=False):
        all_elems = []

        def is_visible(elem):
            return (elem.get_offset_height() != 0 or elem.get_offset_width() != 0)

        def get_offset(elem):
            x, y = 0, 0

            while elem is not None:
                x += elem.get_offset_left() - elem.get_scroll_left()
                y += elem.get_offset_top() - elem.get_scroll_top()
                elem = elem.get_offset_parent()
            return x, y

        cleanup_elems = []

        elem_count = 1

        main_frame = self.webview.get_main_frame()
        webframes = [main_frame] + [main_frame.find_frame(name) for name in self.webframes]
        for frame in webframes:
            dom = frame.get_dom_document()
            if new_window:
                elems = dom.query_selector_all('a')
            else:
                elems = dom.query_selector_all('a, input:not([type=hidden]), textarea, select, button')
            elems = [elems.item(i) for i in
                     range(elems.get_length()) if is_visible(elems.item(i))]
            all_elems.extend(elems)

            overlay = dom.create_element('div')
            html = ''

            for i, elem in enumerate(elems, elem_count):
                css = ''.join([
                    'left: %dpx;',
                    'top: %dpx;',
                    'position: fixed;',
                    'font-size: 13px;',
                    'background-color: #ff6600;',
                    'color: white;',
                    'font-weight: bold;',
                    'padding: 0px 1px;',
                    'border: 2px solid black;',
                    'z-index: 100000;'
                ]) % get_offset(elem)

                span = '<span style="%s">%d</span>' % (css, i)
                html += span
            elem_count += len(elems)

            overlay.set_inner_html(html)

            html_elem = dom.query_selector_all('html').item(0)
            html_elem.append_child(overlay)
            cleanup_elems.append((html_elem, overlay))

        def threaded_prompt():
            try:
                choice = self.roland.prompt("Follow:")
            except AbortPromptError:
                return
            finally:
                for html_elem, overlay in cleanup_elems:
                    html_elem.remove_child(overlay)

            elem = all_elems[int(choice)]

            if elem.get_tag_name().lower() == 'a':
                if new_window:
                    url = elem.get_href()
                    self.roland.new_window(url)
                else:
                    elem.click()
            else:
                elem.focus()
                self.set_mode(Mode.Insert)

        t = threading.Thread(target=threaded_prompt)
        t.start()

    def search_page(self, text=None, forwards=True, case_insensitive=None):
        self.previous_search = text = text or self.roland.prompt('Search:')

        # smart search
        if case_insensitive is None:
            case_insensitive = text.lower() != text

        self.webview.mark_text_matches(text, case_insensitive, 0)
        self.webview.set_highlight_text_matches(True)
        self.webview.search_text(text, case_insensitive, True, True)

    def next_search_result(self, forwards=True, case_insensitive=None):
        if self.previous_search:
            self.search_page(
                text=self.previous_search,
                forwards=forwards,
                case_insensitive=case_insensitive,
            )

    def zoom_in(self):
        self.webview.zoom_in()

    def zoom_out(self):
        self.webview.zoom_out()

    def zoom_reset(self):
        self.webview.set_zoom_level(1)

    def stop(self):
        self.webview.stop_loading()

    def move(self, x=0, y=0):
        self.webview.execute_script('window.scrollBy(%d, %d);' % (x*30, y*30))

    def shell(self):
        t = threading.Thread(target=code.interact, kwargs={'local': locals()})
        t.daemon = True
        t.start()

    def quit(self):
        self.roland.quit()

    def reload(self):
        self.webview.reload()

    def reload_bypass_cache(self):
        self.webview.reload_bypass_cache()

    def cancel_download(self):
        if not self.roland.is_enabled(DownloadManager):
            self.roland.notify("Download manager not enabled")
            return

        if not self.roland.downloads:
            self.roland.notify("No downloads in progress")
            return

        name = self.roland.prompt("Cancel download:", options=self.roland.downloads.keys())

        try:
            download = self.roland.downloads[name]
        except KeyError:
            self.roland.notify("No download by that name")
        else:
            download.cancel()

    def list_downloads(self):
        if not self.roland.is_enabled(DownloadManager):
            self.roland.notify("Download manager not enabled")
            return

        if not self.roland.downloads:
            self.roland.notify("No downloads in progress")
            return

        for location, download in self.roland.downloads.items():
            if download.get_progress() == 1.0:
                continue  # completed while we were doing this
            progress = get_pretty_size(download.get_current_size())
            total = get_pretty_size(download.get_total_size())
            self.roland.notify('%s - %s out of %s' % (location, progress, total))


class StatusLine:
    def __init__(self):
        self.left = Gtk.Label()
        self.middle = Gtk.Label()
        self.right = Gtk.Label()

        self.left.set_alignment(0.0, 0.5)
        self.right.set_alignment(1.0, 0.5)

        self.buffered_command = ''
        self.uri = ''
        self.trusted = True

    def set_uri(self, uri):
        self.uri = uri
        self.update_right()

    def set_mode(self, text):
        self.left.set_markup(text)

    def set_trust(self, trusted):
        self.trusted = trusted
        self.update_right()

    def set_buffered_command(self, text):
        self.buffered_command = text
        self.update_right()

    def update_right(self):
        text = []
        if self.buffered_command:
            text.append('<b>{}</b>'.format(self.buffered_command))

        if self.uri:
            text.append(html.escape(self.uri))

        if not self.trusted:
            text.append('<span foreground="red"><b>untrusted</b></span>')

        self.right.set_markup(' <b>|</b> '.join(text))


class BrowserTitle:
    title = ''
    progress = 0

    def __str__(self):
        if self.progress < 100:
            return '[%d%%] Loading... %s' % (self.progress, self.title)
        return self.title or ''


class AbortPromptError(Exception):
    pass


class BrowserWindow(BrowserCommands, Gtk.Window):
    def __init__(self, roland):
        super().__init__()
        self.roland = roland
        self.previous_search = ''
        self.title = BrowserTitle()
        self.webview = None
        self.sub_commands = None

    @classmethod
    def from_webview(cls, browser, roland):
        self = cls(roland)
        self.webview = browser
        self.webview.connect('web-view-ready', lambda *args: self.start(None))
        return self

    def start(self, url):
        self.set_default_size(1000, 800)
        self.connect('key-press-event', self.on_key_press_event)

        # will already be initialised for popups
        if self.webview is None:
            self.webview = WebKit.WebView()

        settings = self.webview.get_settings()
        settings.props.user_agent = self.roland.config.default_user_agent
        settings.props.enable_running_of_insecure_content = self.roland.config.run_insecure_content
        settings.props.enable_display_of_insecure_content = self.roland.config.display_insecure_content
        stylesheet = 'file://{}'.format(
            config_path('stylesheet.{}.css', self.roland.profile))
        settings.props.user_stylesheet_uri = stylesheet
        self.status_line = StatusLine()

        self.set_mode(Mode.Normal)

        self.webview.connect('notify::title', self.update_title_from_event)
        self.webview.connect('notify::progress', self.update_title_from_event)
        self.webview.connect('notify::load-status', self.on_load_status)
        self.webview.connect('close-web-view', lambda *args: self.destroy())
        self.webview.connect('create-web-view', self.on_create_web_view)
        self.webview.connect('frame-created', self.on_frame_created)
        self.webframes = []

        if self.roland.is_enabled(HistoryManager):
            self.webview.connect('navigation-policy-decision-requested', self.roland.on_navigation_policy_decision_requested)
        self.webview.connect('navigation-policy-decision-requested', self.on_navigation_policy_decision_requested)

        if self.roland.is_enabled(DownloadManager):
            self.webview.connect('download-requested', self.roland.on_download_requested)
            self.webview.connect('mime-type-policy-decision-requested', self.roland.on_mime_type_policy_decision_requested)

        box = Gtk.VBox()
        scrollable = Gtk.ScrolledWindow()
        scrollable.add(self.webview)
        box.pack_start(scrollable, True, True, 0)

        status_line = Gtk.HBox()
        status_line.add(self.status_line.left)
        status_line.add(self.status_line.middle)
        status_line.add(self.status_line.right)

        box.pack_end(status_line, False, False, 0)

        self.add(box)
        self.show_all()

        # will be None for popups
        if url is not None:
            self.open_or_search(url)

    def on_load_status(self, webview, load_status):
        if self.webview.get_load_status() == WebKit.LoadStatus.FINISHED:
            if self.webview.get_uri().startswith('http://'):
                return
            main_frame = self.webview.get_main_frame()
            data_source = main_frame.get_data_source()
            network_request = data_source.get_request()
            soup_message = network_request.get_message()

            if (soup_message.get_flags() & Soup.MessageFlags.CERTIFICATE_TRUSTED):
                self.status_line.set_trust(True)
            else:
                self.status_line.set_trust(False)

    def on_navigation_policy_decision_requested(
            self, webview, frame, request, navigation_action, policy_decision):
        uri = request.get_uri()
        if self.webview.get_main_frame() == frame:
            self.status_line.set_uri(uri)

    def update_title_from_event(self, widget, event):
        if event.name == 'title':
            title = self.webview.get_title()
            self.title.title = title
        elif event.name == 'progress':
            self.title.progress = int(self.webview.get_progress() * 100)

        self.set_title(str(self.title))

    def on_frame_created(self, webview, webframe):
        self.webframes.append(webframe.get_name())

    def on_create_web_view(self, webview, webframe):
        if self.roland.hooks('should_open_popup', webframe.get_uri(), default=True):
            v = WebKit.WebView()
            self.roland.windows.append(BrowserWindow.from_webview(v, self.roland))
            return v

    def on_key_press_event(self, widget, event):
        def get_keyname():
            keyname = Gdk.keyval_name(event.keyval)
            fields = []
            if event.state & Gdk.ModifierType.CONTROL_MASK:
                fields.append('C')
            if event.state & Gdk.ModifierType.SUPER_MASK:
                fields.append('L')
            if event.state & Gdk.ModifierType.MOD1_MASK:
                fields.append('A')

            fields.append(keyname)
            return '-'.join(fields)

        keyname = get_keyname()

        if keyname in ('Shift_L', 'Shift_R'):
            return

        if self.mode in (Mode.Normal, Mode.SubCommand):
            available_commands = {
                Mode.Normal: self.roland.config.commands,
                Mode.SubCommand: self.sub_commands,
            }[self.mode]

            orig_mode = self.mode

            try:
                command = available_commands[keyname]
            except KeyError:
                pass
            else:
                try:
                    with contextlib.suppress(AbortPromptError):
                        return command(self)
                except Exception as e:
                    self.roland.notify("Error invoking command '{}': {}'".format(keyname, e))
                    traceback.print_exc()
            finally:
                if orig_mode == Mode.SubCommand:
                    self.set_mode(Mode.Normal)
                    self.sub_commands = None
        else:
            assert self.mode == Mode.Insert

            if keyname == 'Escape':
                self.set_mode(Mode.Normal)

    def set_mode(self, mode, *args):
        assert mode in Mode
        self.mode = mode

        if mode == Mode.Normal:
            self.webview.set_can_focus(False)
            self.set_focus(None)
            self.status_line.set_mode('<b>NORMAL</b>')
            self.status_line.set_buffered_command('')
        elif mode == Mode.SubCommand:
            command, self.sub_commands = args

            self.webview.set_can_focus(False)
            self.set_focus(None)
            self.status_line.set_mode('<b>COMMAND</b>')
            self.status_line.set_buffered_command(command)
        elif mode == Mode.Command:
            try:
                command = list(shlex.split(self.roland.prompt('Command:', options=self.roland.get_commands(), default_first=False)))
                command_name, args = command[0], command[1:]
                self.run_command(command_name, *args)
            finally:
                self.set_mode(Mode.Normal)
        else:
            assert mode == Mode.Insert, "Unknown Mode %s" % mode
            self.webview.set_can_focus(True)
            self.webview.grab_focus()
            self.status_line.set_mode('<b>INSERT</b>')
            self.status_line.set_buffered_command('')
            # stop event propagation to prevent dumping 'i' into webpage
            return True

    def run_command(self, name, *args):
        try:
            command = getattr(self, name)
        except AttributeError:
            self.roland.notify("No such command '{}'".format(name))
            return

        try:
            command(*args)
        except AbortPromptError:
            pass
        except Exception as e:
            self.roland.notify("Error calling '{}': {}".format(name, str(e)))
            traceback.print_exc()


class HistoryManager:
    def setup(self):
        self.create_history_db()
        getattr(super(), 'setup', lambda: None)()

    def create_history_db(self):
        conn = self.get_history_db()

        cursor = conn.cursor()
        cursor.execute('create table if not exists history '
                       '(url text, view_count integer)')
        conn.commit()
        conn.close()

    def get_history_db(self):
        return sqlite3.connect(config_path('history.{}.db', self.profile))

    def on_navigation_policy_decision_requested(
            self, webview, frame, request, navigation_action, policy_decision):
        uri = request.get_uri()
        conn = self.get_history_db()
        cursor = conn.cursor()

        cursor.execute('select url from history where url = ?', (uri,))
        rec = cursor.fetchone()

        if rec is None:
            cursor.execute('insert into history (url, view_count)'
                           'values (?, 1)', (uri,))
        else:
            cursor.execute('update history set view_count = view_count + 1 '
                           'where url = ?', (uri,))
        conn.commit()
        conn.close()

        self.webframes = []

        return False


class DownloadManager:
    save_location = os.path.expanduser('~/Downloads/')

    def setup(self):
        self.downloads = {}
        getattr(super(), 'setup', lambda: None)()

    def on_download_requested(self, browser, download):
        save_path = os.path.join(self.save_location,
                                 download.get_suggested_filename())

        orig_save_path = save_path
        for i in itertools.count(1):
            if os.path.exists(save_path):
                save_path = orig_save_path + ('.%d' % i)
            else:
                break

        try:
            location = self.prompt(
                "Download location (%s):" % download.get_uri(), options=[save_path])
        except AbortPromptError:
            return False

        download.connect('notify::status', self.download_status_changed, location)
        download.set_destination_uri('file://' + location)
        self.downloads[location] = download
        return True

    def download_status_changed(self, download, status, location):
        if download.get_status() == WebKit.DownloadStatus.FINISHED:
            self.notify('Download finished: %s' % location)
            self.downloads.pop(location)
        elif download.get_status() == WebKit.DownloadStatus.ERROR:
            self.notify('Download failed: %s' % location, critical=True)
            self.downloads.pop(location)
        elif download.get_status() == WebKit.DownloadStatus.CANCELLED:
            self.downloads.pop(location)

            self.notify('Download cancelled: %s' % location)
            try:
                os.unlink(location)
            except OSError:
                pass

    def on_mime_type_policy_decision_requested(
            self, browser, frame, request, mime_type, policy_decision):
        if browser.can_show_mime_type(mime_type):
            return False
        policy_decision.download()
        return True


class CookieManager:
    def setup(self):
        self.cookiejar = Soup.CookieJarDB.new(
            config_path('cookies.{}.db', self.profile), False)
        self.cookiejar.set_accept_policy(Soup.CookieJarAcceptPolicy.ALWAYS)
        self.session = WebKit.get_default_session()
        self.session.add_feature(self.cookiejar)
        getattr(super(), 'setup', lambda: None)()


class SessionManager:
    def setup(self):
        try:
            with open(config_path('session.{}.json', self.profile), 'r') as f:
                session = json.load(f)
        except FileNotFoundError:
            pass
        except Exception as e:
            self.notify("Error loading session: {}".format(e))
        else:
            for page in session:
                self.do_new_browser(page['uri'])

        self.connect('shutdown', self.session_on_shutdown)
        getattr(super(), 'setup', lambda: None)()

    def session_on_shutdown(self, app):
        self.save_session()

    def save_session(self):
        session = []
        for window in self.get_windows():
            uri = window.webview.get_uri()

            if uri is not None:
                session.append({'uri': uri})

        with open(config_path('session.{}.json', self.profile), 'w') as f:
            json.dump(session, f, indent=4)


class DBusManager:
    def before_run(self):
        try:
            from dbus.mainloop.glib import DBusGMainLoop
        except ImportError:
            pass
        else:
            DBusGMainLoop(set_as_default=True)
        getattr(super(), 'before_run', lambda: None)()

    def setup(self):
        try:
            self.create_dbus_api()
        except Exception as e:
            pass
        getattr(super(), 'setup', lambda: None)()

    def create_dbus_api(self):
        import dbus
        import dbus.service

        roland = self

        class DBusAPI(dbus.service.Object):
            def __init__(self):
                bus_name = dbus.service.BusName('com.deschain.roland.{}'.format(roland.profile), bus=dbus.SessionBus())
                dbus.service.Object.__init__(self, bus_name, '/com/deschain/roland/{}'.format(roland.profile))

            @dbus.service.method('com.deschain.roland.{}'.format(roland.profile))
            def open_window(self, url):
                roland.do_new_browser(url)
                return 1

        self.roland_api = DBusAPI()


class Roland(HistoryManager, SessionManager, DownloadManager, CookieManager, Gtk.Application):
    __gsignals__ = {
        'new_browser': (GObject.SIGNAL_RUN_LAST, None, (str,)),
    }

    def __init__(self):
        Gtk.Application.__init__(self, application_id='deschain.roland', flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.setup_run = False
        self.connect('command-line', self.on_command_line)

        self.before_run()

    def before_run(self):
        getattr(super(), 'before_run', lambda: None)()

    def do_new_browser(self, url):
        window = BrowserWindow(self)
        window.start(url)
        self.add_window(window)

    def set_profile(self, profile):
        self.profile = profile
        self.set_application_id('{}.{}'.format('deschain.roland', profile))

    def emit(self, *args):
        '''A thread safe emit.

        This is so follow(url, new_window=True) works as it requires a thread.
        '''
        GObject.idle_add(GObject.GObject.emit, self, *args)

    def load_config(self):
        self.config = imp.load_source('roland.config', config_path('config.py'))

        if not hasattr(self.config, 'default_user_agent') or self.config.default_user_agent is None:
            self.config.default_user_agent = WebKit.WebSettings().props.user_agent
        if not hasattr(self.config, 'run_insecure_content'):
            self.config.run_insecure_content = WebKit.WebSettings().props.enable_running_of_insecure_content
        if not hasattr(self.config, 'display_insecure_content'):
            self.config.display_insecure_content = WebKit.WebSettings().props.enable_display_of_insecure_content

    def setup(self):
        if self.setup_run:
            return

        self.setup_run = True

        try:
            import setproctitle
            setproctitle.setproctitle('roland')
        except Exception:
            pass

        try:
            os.makedirs(config_path(''))
        except OSError:
            pass

        self.load_config()
        getattr(super(), 'setup', lambda: None)()

    def is_enabled(self, cls):
        return isinstance(self, cls)

    def on_command_line(self, roland, command_line):
        self.setup()

        urls = command_line.get_arguments()[1:]
        if not urls:
            # if we're just loading up a new window from a remote invocation,
            # or the session was empty
            if command_line.get_is_remote() or not self.get_windows():
                urls = [self.config.home_page]

        for url in urls:
            self.do_new_browser(url)

        return 0

    def new_window(self, url):
        self.emit('new-browser', url)

    def notify(self, message, critical=False, header=''):
        if not Notify.is_initted():
            Notify.init('roland')
        n = Notify.Notification.new(header, message)
        if critical:
            n.set_urgency(Notify.Urgency.CRITICAL)
        n.show()

    def get_commands(self):
        return [f for f in dir(BrowserCommands) if not f.startswith('__')]

    def set_clipboard(self, text, notify=True):
        primary = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
        secondary = Gtk.Clipboard.get(Gdk.SELECTION_SECONDARY)
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

        primary.set_text(text, -1)
        secondary.set_text(text, -1)
        clipboard.set_text(text, -1)

        if notify:
            self.notify("Set clipboard to '{}'".format(text))

    def prompt_yes_no(self, message):
        return self.hooks('prompt_yes_no', message, ['yes', 'no'])

    def prompt(self, message, options=(), default_first=True):
        return self.hooks('prompt', message, options, default_first)

    def most_popular_urls(self):
        if not self.is_enabled(HistoryManager):
            return []
        conn = self.get_history_db()
        cursor = conn.cursor()
        cursor.execute('select url from history order by view_count desc limit 50')
        urls = [url for (url,) in cursor.fetchall()]
        conn.close()
        return urls

    def select_window(self):
        windows = {'%d: %s' % (i, w.title.title): w for (i, w) in enumerate(self.get_windows())}
        win = self.prompt("Window", options=sorted(windows), default_first=False)
        win = windows[win]
        win.window.present()

    def hooks(self, name, *args, default=None):
        return getattr(self.config, name, lambda *args: default)(*args)

    def change_user_agent(self, user_agent=None):
        if user_agent is None:
            user_agents = [self.roland.config.default_user_agent] + self.hooks('user_agent_choices', default=[])
            user_agent = user_agent or self.prompt("User Agent:", options=user_agents)

        for window in self.get_windows():
            window.web_view.get_settings().props.user_agent = user_agent

    def quit(self):
        if self.is_enabled(DownloadManager) and self.downloads:
            quit = self.prompt_yes_no('Do you really want to quit? You have %d downloads running.' % len(self.downloads))

            if not quit:
                return

            while self.downloads:
                downloads = list(self.downloads.values())
                if not downloads:
                    break
                download = downloads[0]
                if download.get_progress() != 1.0:
                    download.cancel()

        Gtk.Application.quit(self)


def get_pretty_size(bytecount):
    size = bytecount

    for suffix in ['b', 'kb', 'mb', 'gb', 'tb', 'pb']:
        if size // 1024 < 1:
            return '%d%s' % (size, suffix)
        size /= 1024
    return '%d%s' % (size, suffix)


def config_path(t, profile=''):
    t = t.format(profile)
    return os.path.expanduser('~/.config/roland/{}'.format(t))

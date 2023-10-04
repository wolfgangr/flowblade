"""
    Flowblade Movie Editor is a nonlinear video editor.
    Copyright 2012 Janne Liljeblad.

    This file is part of Flowblade Movie Editor <https://github.com/jliljebl/flowblade/>.

    Flowblade Movie Editor is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    Flowblade Movie Editor is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with Flowblade Movie Editor.  If not, see <http://www.gnu.org/licenses/>.
"""


from gi.repository import Gtk, GLib, Gdk, GdkPixbuf
from gi.repository import GObject
from gi.repository import Pango

import os
import subprocess
import sys
import time
import threading
try:
    import mlt7 as mlt
except:
    import mlt

import appconsts
import editorlayout
import editorpersistance
from editorstate import PROJECT
import gui
import guicomponents
import guipopover
import guiutils
import motionheadless
import persistance
import proxyheadless
import renderconsumer
import respaths
import userfolders
import utils

QUEUED = 0
RENDERING = 1
COMPLETED = 2
CANCELLED = 3

NOT_SET_YET = 0
CONTAINER_CLIP_RENDER_GMIC = 1
CONTAINER_CLIP_RENDER_MLT_XML = 2
CONTAINER_CLIP_RENDER_BLENDER = 3 # Deprecated
MOTION_MEDIA_ITEM_RENDER = 4
PROXY_RENDER = 5
CONTAINER_CLIP_RENDER_FLUXITY = 6

open_media_file_callback = None

_status_polling_thread = None

_jobs_list_view = None

_jobs = [] # proxy objects that represent background renders and provide info on render status.
_remove_list = [] # objects are removed from GUI with delay to give user time to notice copmpletion

_jobs_render_progress_window = None


class JobProxy: # This object represents job in job queue. 


    def __init__(self, uid, callback_object):
        self.proxy_uid = uid
        self.type = NOT_SET_YET 
        self.status = RENDERING
        self.progress = 0.0 # 0.0. - 1.0
        self.text = ""
        self.elapsed = 0.0 # in fractional seconds

        # callback_object have to implement interface:
        #     start_render()
        #     update_render_status()
        #     abort_render()
        self.callback_object = callback_object

    def get_elapsed_str(self):
        return utils.get_time_str_for_sec_float(self.elapsed)

    def get_type_str(self):
        if self.type == NOT_SET_YET:
            return "NO TYPE SET" # this just error info, application has done something wrong.
        elif self.type == CONTAINER_CLIP_RENDER_GMIC:
            return _("G'Mic Clip")
        elif self.type == CONTAINER_CLIP_RENDER_MLT_XML:
            return _("Selection Clip")
        elif self.type == MOTION_MEDIA_ITEM_RENDER:
            return _("Motion Clip")
        elif self.type == PROXY_RENDER:
            return _("Proxy Clip")
        elif self.type == CONTAINER_CLIP_RENDER_FLUXITY:
            return _("Generator Clip")
            
    def get_progress_str(self):
        if self.progress < 0.0:
            return "-"
        return str(int(self.progress * 100.0)) + "%"

    def start_render(self):
        self.callback_object.start_render()
        
    def abort_render(self):
        self.callback_object.abort_render()


class JobQueueMessage:  # Jobs communicate with job queue by sending these objects.
    
    def __init__(self, uid, job_type, status, progress, text, elapsed):
        self.proxy_uid = uid       
        self.type = job_type 
        self.status = status
        self.progress = progress
        self.text = text
        self.elapsed = elapsed

                  
#---------------------------------------------------------------- interface
def add_job(job_proxy):
    global _jobs, _jobs_list_view 
    _jobs.append(job_proxy)
    _jobs_list_view.fill_data_model()
    if editorpersistance.prefs.open_jobs_panel_on_add == True:
        editorlayout.show_panel(appconsts.PANEL_JOBS)
    
    if editorpersistance.prefs.render_jobs_sequentially == False: # Feature not active for first release 2.6.
        job_proxy.start_render()
    else:
         running = _get_jobs_with_status(RENDERING)
         if len(running) == 0:
             job_proxy.start_render()

    # Get polling going if needed.
    global _status_polling_thread
    if _status_polling_thread == None:
        _status_polling_thread = ContainerStatusPollingThread()
        _status_polling_thread.start()

def update_job_queue(job_msg): # We're using JobProxy objects as messages to update values on jobs in _jobs list.
    global _jobs_list_view, _remove_list
    row = -1
    for i in range (0, len(_jobs)):

        if _jobs[i].proxy_uid == job_msg.proxy_uid:
            if _jobs[i].status == CANCELLED:
                return # it is maybe possible to get update attempts here after cancellation.         
            # Remember job row
            row = i
            break

    if row == -1:
        # Something is wrong.
        print("trying to update non-existing job at jobs.show_message()!")
        return

    # Copy values
    _jobs[row].text = job_msg.text
    _jobs[row].elapsed = job_msg.elapsed
    _jobs[row].progress = job_msg.progress

    if job_msg.status == COMPLETED:
        _jobs[row].status = COMPLETED
        _jobs[row].text = _("Completed")
        _jobs[row].progress = 1.0
        _remove_list.append(_jobs[row])
        GLib.timeout_add(4000, _remove_jobs)
        waiting_jobs = _get_jobs_with_status(QUEUED)
        if len(waiting_jobs) > 0:
            waiting_jobs[0].start_render()
    else:
        _jobs[row].status = job_msg.status

    tree_path = Gtk.TreePath.new_from_string(str(row))
    store_iter = _jobs_list_view.storemodel.get_iter(tree_path)

    _jobs_list_view.storemodel.set_value(store_iter, 0, _jobs[row].get_type_str())
    _jobs_list_view.storemodel.set_value(store_iter, 1, _jobs[row].text)
    _jobs_list_view.storemodel.set_value(store_iter, 2, _jobs[row].get_elapsed_str())
    _jobs_list_view.storemodel.set_value(store_iter, 3, _jobs[row].get_progress_str())

    _jobs_list_view.scroll.queue_draw()

def _cancel_all_jobs():
    global _jobs, _remove_list
    _remove_list = []
    for job in _jobs:
        if job.status == RENDERING:
            job.abort_render()
        job.progress = -1.0
        job.text = _("Cancelled")
        job.status = CANCELLED
        _remove_list.append(job)

    _jobs_list_view.fill_data_model()
    _jobs_list_view.scroll.queue_draw()
    GLib.timeout_add(4000, _remove_jobs)
        
def get_jobs_of_type(job_type):
    jobs_of_type = []
    for job in _jobs:
        job.type = job_type
        jobs_of_type.append(job)
    
    return jobs_of_type

def proxy_render_ongoing():
    proxy_jobs = get_jobs_of_type(PROXY_RENDER)
    if len(proxy_jobs) == 0:
        return False
    else:
        return True

def create_jobs_list_view():
    global _jobs_list_view
    _jobs_list_view = JobsQueueView()
    return _jobs_list_view

def get_jobs_panel():
    global _jobs_list_view

    actions_menu = guicomponents.HamburgerPressLaunch(_menu_action_pressed)
    actions_menu.do_popover_callback = True
    guiutils.set_margins(actions_menu.widget, 8, 2, 2, 18)

    row2 =  Gtk.HBox()
    row2.pack_start(actions_menu.widget, False, True, 0)
    row2.pack_start(Gtk.Label(), True, True, 0)

    panel = Gtk.VBox()
    panel.pack_start(_jobs_list_view, True, True, 0)
    panel.pack_start(row2, False, True, 0)
            
    return panel

def get_active_jobs_count():
    return len(_jobs)



# ------------------------------------------------------------- module functions
def _menu_action_pressed(launcher, widget, event, data):
    guipopover.jobs_menu_popover_show(launcher, widget, _hamburger_item_activated)
    
def _hamburger_item_activated(action, variant, msg=None):
    print(msg)
    if msg == "cancel_all":
        _cancel_all_jobs()

    elif msg == "cancel_selected":
        try:
            jobs_list_index = _jobs_list_view.get_selected_row_index()
        except:
            return # nothing was selected
        
        job = _jobs[jobs_list_index]
        job.abort_render()
        job.progress = -1.0
        job.text = _("Cancelled")
        job.status = CANCELLED
        _remove_list.append(job)

        _jobs_list_view.fill_data_model()
        _jobs_list_view.scroll.queue_draw()
        GLib.timeout_add(4000, _remove_jobs)
        
    elif msg == "open_on_add":
        new_state = not(action.get_state().get_boolean())
        editorpersistance.prefs.open_jobs_panel_on_add = new_state
        editorpersistance.save()
        action.set_state(GLib.Variant.new_boolean(new_state))

def _get_jobs_with_status(status):
    running = []
    for job in _jobs:
        if job.status == status:
            running.append(job)
    
    return running

def _remove_jobs():
    global _jobs, _remove_list
    for  job in _remove_list:
        if job in _jobs:
            _jobs.remove(job)
        else:
            pass

    running = _get_jobs_with_status(RENDERING)
    if len(running) == 0:
        in_queue = _get_jobs_with_status(QUEUED)
        if len(in_queue) > 0:
            in_queue[0].start_render()

    _jobs_list_view.fill_data_model()
    _jobs_list_view.scroll.queue_draw()

    _remove_list = []


# --------------------------------------------------------- GUI 
class JobsQueueView(Gtk.VBox):

    def __init__(self):
        GObject.GObject.__init__(self)
        
        self.storemodel = Gtk.ListStore(str, str, str, str)
        
        # Scroll container
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_shadow_type(Gtk.ShadowType.ETCHED_IN)

        # View
        self.treeview = Gtk.TreeView(model=self.storemodel)
        self.treeview.set_property("rules_hint", True)
        self.treeview.set_headers_visible(True)
        tree_sel = self.treeview.get_selection()
        tree_sel.set_mode(Gtk.SelectionMode.MULTIPLE)

        self.text_rend_1 = Gtk.CellRendererText()
        self.text_rend_1.set_property("ellipsize", Pango.EllipsizeMode.END)

        self.text_rend_2 = Gtk.CellRendererText()
        self.text_rend_2.set_property("yalign", 0.0)
        self.text_rend_2.set_property("ellipsize", Pango.EllipsizeMode.END)
        
        self.text_rend_3 = Gtk.CellRendererText()
        self.text_rend_3.set_property("yalign", 0.0)
        
        self.text_rend_4 = Gtk.CellRendererText()
        self.text_rend_4.set_property("yalign", 0.0)

        # Column views
        self.text_col_1 = Gtk.TreeViewColumn(_("Job Type"))
        self.text_col_2 = Gtk.TreeViewColumn(_("Info"))
        self.text_col_3 = Gtk.TreeViewColumn(_("Render Time"))
        self.text_col_4 = Gtk.TreeViewColumn(_("Progress"))

        #self.text_col_1.set_expand(True)
        self.text_col_1.set_spacing(5)
        self.text_col_1.set_sizing(Gtk.TreeViewColumnSizing.GROW_ONLY)
        self.text_col_1.set_min_width(200)
        self.text_col_1.pack_start(self.text_rend_1, True)
        self.text_col_1.add_attribute(self.text_rend_1, "text", 0) # <- note column index

        self.text_col_2.set_expand(True)
        self.text_col_2.pack_start(self.text_rend_2, True)
        self.text_col_2.add_attribute(self.text_rend_2, "text", 1)
        self.text_col_2.set_min_width(90)

        self.text_col_3.set_expand(False)
        self.text_col_3.pack_start(self.text_rend_3, True)
        self.text_col_3.add_attribute(self.text_rend_3, "text", 2)

        self.text_col_4.set_expand(False)
        self.text_col_4.pack_start(self.text_rend_4, True)
        self.text_col_4.add_attribute(self.text_rend_4, "text", 3)

        # Add column views to view
        self.treeview.append_column(self.text_col_1)
        self.treeview.append_column(self.text_col_2)
        self.treeview.append_column(self.text_col_3)
        self.treeview.append_column(self.text_col_4)

        # Build widget graph and display
        self.scroll.add(self.treeview)
        self.pack_start(self.scroll, True, True, 0)
        self.scroll.show_all()
        self.show_all()

    def get_selected_row_index(self):
        model, rows = self.treeview.get_selection().get_selected_rows()
        return int(rows[0].to_string ())
        
    def fill_data_model(self):
        self.storemodel.clear()        
        
        for job in _jobs:
            row_data = [job.get_type_str(),
                        job.text,
                        job.get_elapsed_str(),
                        job.get_progress_str()]
            self.storemodel.append(row_data)
            self.scroll.queue_draw()



# ------------------------------------------------------------------------------- JOBS QUEUE OBJECTS
# These objects satisfy combined interface as jobs.JobProxy callback_objects and as update polling objects.
#
#     start_render()
#     update_render_status()
#     abort_render()
# 
# ------------------------------------------------------------------------------- JOBS QUEUE OBJECTS


class AbstractJobQueueObject(JobProxy):
    
    def __init__(self, session_id, job_type):
        self.session_id = session_id 
        JobProxy.__init__(self, session_id, self)

        self.type = job_type

    def get_session_id(self):
        return self.session_id
        
    def get_job_name(self):
        return "job name"
    
    def add_to_queue(self):
        add_job(self.create_job_queue_proxy())

    def get_job_queue_message(self):
        job_queue_message = JobQueueMessage(self.proxy_uid, self.type, self.status,
                                            self.progress, self.text, self.elapsed)
        return job_queue_message

    def create_job_queue_proxy(self):
        self.status = QUEUED
        self.progress = 0.0
        self.elapsed = 0.0 # jobs does not use this value
        self.text = _("In Queue - ") + " " + self.get_job_name()
        return self
        
    def get_completed_job_message(self):
        job_queue_message = self.get_job_queue_message()
        job_queue_message.status = COMPLETED
        job_queue_message.progress = 1.0
        job_queue_message.elapsed = 0.0 # jobs does not use this value
        job_queue_message.text = "dummy" # this will be overwritten with completion message
        return job_queue_message



class MotionRenderJobQueueObject(AbstractJobQueueObject):

    def __init__(self, session_id, write_file, args):
        
        AbstractJobQueueObject.__init__(self, session_id, MOTION_MEDIA_ITEM_RENDER)
        
        self.write_file = write_file
        self.args = args
        self.parent_folder = userfolders.get_temp_render_dir() # THis is just used for message passing, output file goes where user decided.

    def get_job_name(self):
        folder, file_name = os.path.split(self.write_file)
        return file_name
        
    def start_render(self):
        
        job_msg = self.get_job_queue_message()
        job_msg.text = _("Render Starting...")
        job_msg.status = RENDERING
        update_job_queue(job_msg)
        
        # Create command list and launch process.
        command_list = [sys.executable]
        command_list.append(respaths.LAUNCH_DIR + "flowblademotionheadless")
        for arg in self.args:
            command_list.append(arg)
        parent_folder_arg = "parent_folder:" + str(self.parent_folder)
        command_list.append(parent_folder_arg)
            
        subprocess.Popen(command_list)
        
    def update_render_status(self):
        GLib.idle_add(self._update_from_gui_thread)
            
    def _update_from_gui_thread(self):

        if motionheadless.session_render_complete(self.parent_folder, self.get_session_id()) == True:
            #remove_as_status_polling_object(self)
            
            job_msg = self.get_completed_job_message()
            update_job_queue(job_msg)
            
            motionheadless.delete_session_folders(self.parent_folder, self.get_session_id())
            
            GLib.idle_add(self.create_media_item)

        else:
            status = motionheadless.get_session_status(self.parent_folder, self.get_session_id())
            if status != None:
                fraction, elapsed = status
                
                self.progress = float(fraction)
                if self.progress > 1.0:
                    # A fix for how progress is calculated in gmicheadless because producers can render a bit longer then required.
                    self.progress = 1.0

                self.elapsed = float(elapsed)
                self.text = self.get_job_name()
                
                job_msg = self.get_job_queue_message()
                
                update_job_queue(job_msg)
            else:
                # Process start/stop on their own and we hit trying to get non-existing status for e.g completed renders.
                pass
    
    def abort_render(self):
        #remove_as_status_polling_object(self)
        motionheadless.abort_render(self.parent_folder, self.get_session_id())
        
    def create_media_item(self):
        open_media_file_callback(self.write_file)
 

class ProxyRenderJobQueueObject(AbstractJobQueueObject):

    def __init__(self, session_id, render_data):
        
        AbstractJobQueueObject.__init__(self, session_id, PROXY_RENDER)
        
        self.render_data = render_data
        self.parent_folder = userfolders.get_temp_render_dir()

    def get_job_name(self):
        folder, file_name = os.path.split(self.render_data.media_file_path)
        return file_name
        
    def start_render(self):
        job_msg = self.get_job_queue_message()
        job_msg.text = _("Render Starting...")
        job_msg.status = RENDERING
        update_job_queue(job_msg)
        
        # Create command list and launch process.
        command_list = [sys.executable]
        command_list.append(respaths.LAUNCH_DIR + "flowbladeproxyheadless")
        args = self.render_data.get_data_as_args_tuple()

        # Info print, try to remove later.
        proxy_profile_path = userfolders.get_cache_dir() + "temp_proxy_profile"
        proxy_profile = mlt.Profile(proxy_profile_path)
        enc_index = int(utils.get_headless_arg_value(args, "enc_index"))
        print("PROXY FFMPEG ARGS: ", renderconsumer.proxy_encodings[enc_index].get_args_vals_tuples_list(proxy_profile))

        for arg in args:
            command_list.append(arg)
            
        session_arg = "session_id:" + str(self.session_id)
        command_list.append(session_arg)

        parent_folder_arg = "parent_folder:" + str(self.parent_folder)
        command_list.append(parent_folder_arg)

        subprocess.Popen(command_list)
    
    def update_render_status(self):

        GLib.idle_add(self._update_from_gui_thread)
            
    def _update_from_gui_thread(self):
        
        if proxyheadless.session_render_complete(self.parent_folder, self.get_session_id()) == True:
            
            job_msg = self.get_completed_job_message()
            update_job_queue(job_msg)
            
            proxyheadless.delete_session_folders(self.parent_folder, self.get_session_id()) # these were created mltheadlessutils.py, see proxyheadless.py
            
            GLib.idle_add(self.proxy_render_complete)

        else:
            status = proxyheadless.get_session_status(self.parent_folder, self.get_session_id())
            if status != None:
                fraction, elapsed = status
                
                self.progress = float(fraction)
                if self.progress > 1.0:
                    self.progress = 1.0

                self.elapsed = float(elapsed)
                self.text = self.get_job_name()

                job_msg = self.get_job_queue_message()
                
                update_job_queue(job_msg)
            else:
                # Process start/stop on their own and we hit trying to get non-existing status for e.g completed renders.
                pass
    
    def abort_render(self):
        # remove_as_status_polling_object(self)
        motionheadless.abort_render(self.parent_folder, self.get_session_id())
        
    def proxy_render_complete(self):
        try:
            media_file = PROJECT().media_files[self.render_data.media_file_id]
        except:
            # User has deleted media file before proxy render complete
            return

        media_file.add_proxy_file(self.render_data.proxy_file_path)

        if PROJECT().proxy_data.proxy_mode == appconsts.USE_PROXY_MEDIA: # When proxy mode is USE_PROXY_MEDIA all proxy files are used all the time
            media_file.set_as_proxy_media_file()
        
            # if the rendered proxy file was the last proxy file being rendered,
            # auto re-convert to update proxy clips.
            proxy_jobs = get_jobs_of_type(PROXY_RENDER)
            if len(proxy_jobs) == 0:
                self.render_data.do_auto_re_convert_func()
            elif len(proxy_jobs) == 1:
                self.render_data.do_auto_re_convert_func()



# ----------------------------------------------------------------- polling
class ContainerStatusPollingThread(threading.Thread):
    
    def __init__(self):

        self.abort = False

        threading.Thread.__init__(self)

    def run(self):

        while self.abort == False:
            for job in _jobs:
                if job.status == RENDERING:
                    job.callback_object.update_render_status() # Make sure these methods enter/exit Gtk threads.

            # Handling post-app-close jobs rendering.
            if _jobs_render_progress_window != None and len(_jobs) != 0:
                _jobs_render_progress_window.update_render_progress()
            elif _jobs_render_progress_window != None and len(_jobs) == 0:
                _jobs_render_progress_window.jobs_completed()
                self.abort = True
                
            time.sleep(0.5)

    def shutdown(self):
        for job in _jobs:
            job.abort_render()
        
        self.abort = True

def shutdown_polling():
    if _status_polling_thread == None:
        return
    
    _status_polling_thread.shutdown()


# coding=utf-8
from __future__ import absolute_import

import octoprint.plugin
from octoprint.server import user_permission
import socket
import json
import logging
import os
import re
import threading
import time
import pywemo 

class wemosmartplugPlugin(octoprint.plugin.SettingsPlugin,
                            octoprint.plugin.AssetPlugin,
                            octoprint.plugin.TemplatePlugin,
							octoprint.plugin.SimpleApiPlugin,
							octoprint.plugin.StartupPlugin):
							
	def __init__(self):
		self._logger = logging.getLogger("octoprint.plugins.wemosmartplug")
		self._wemosmartplug_logger = logging.getLogger("octoprint.plugins.wemosmartplug.debug")
							
	##~~ StartupPlugin mixin
	
	def on_startup(self, host, port):
		# setup customized logger
		from octoprint.logging.handlers import CleaningTimedRotatingFileHandler
		wemosmartplug_logging_handler = CleaningTimedRotatingFileHandler(self._settings.get_plugin_logfile_path(postfix="debug"), when="D", backupCount=3)
		wemosmartplug_logging_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
		wemosmartplug_logging_handler.setLevel(logging.DEBUG)

		self._wemosmartplug_logger.addHandler(wemosmartplug_logging_handler)
		self._wemosmartplug_logger.setLevel(logging.DEBUG if self._settings.get_boolean(["debug_logging"]) else logging.INFO)
		self._wemosmartplug_logger.propagate = False
	
	def on_after_startup(self):
		self._logger.info("WemoSmartplug loaded!")
	
	##~~ SettingsPlugin mixin
	
	def get_settings_defaults(self):
		return dict(
			debug_logging = False,
			arrSmartplugs = [{'ip':'','label':'','icon':'icon-bolt','displayWarning':True,'warnPrinting':False,'gcodeEnabled':False,'gcodeOnDelay':0,'gcodeOffDelay':0,'autoConnect':True,'autoConnectDelay':10.0,'autoDisconnect':True,'autoDisconnectDelay':0,'sysCmdOn':False,'sysRunCmdOn':'','sysCmdOnDelay':0,'sysCmdOff':False,'sysRunCmdOff':'','sysCmdOffDelay':0,'currentState':'unknown','btnColor':'#808080'}],
			pollingInterval = 15,
			pollingEnabled = False
		)
		
	def on_settings_save(self, data):	
		old_debug_logging = self._settings.get_boolean(["debug_logging"])

		octoprint.plugin.SettingsPlugin.on_settings_save(self, data)

		new_debug_logging = self._settings.get_boolean(["debug_logging"])
		if old_debug_logging != new_debug_logging:
			if new_debug_logging:
				self._wemosmartplug_logger.setLevel(logging.DEBUG)
			else:
				self._wemosmartplug_logger.setLevel(logging.INFO)
				
	def get_settings_version(self):
		return 4
		
	def on_settings_migrate(self, target, current=None):
		if current is None or current < self.get_settings_version():
			# Reset plug settings to defaults.
			self._logger.debug("Resetting arrSmartplugs for wemosmartplug settings.")
			self._settings.set(['arrSmartplugs'], self.get_settings_defaults()["arrSmartplugs"])
		
	##~~ AssetPlugin mixin

	def get_assets(self):
		return dict(
			js=["js/wemosmartplug.js"],
			css=["css/wemosmartplug.css"]
		)
		
	##~~ TemplatePlugin mixin
	
	def get_template_configs(self):
		return [
			dict(type="navbar", custom_bindings=True),
			dict(type="settings", custom_bindings=True)
		]
		
	##~~ SimpleApiPlugin mixin
	
	def turn_on(self, plugip):
		self._wemosmartplug_logger.debug("Turning on %s." % plugip)
		plug = self.plug_search(self._settings.get(["arrSmartplugs"]),"ip",plugip)
		self._wemosmartplug_logger.debug(plug)		
		chk = self.sendCommand("on",plugip)
		if chk == 0:
			self.check_status(plugip)
			if plug["autoConnect"]:
				t = threading.Timer(int(plug["autoConnectDelay"]),self._printer.connect)
				t.start()
			if plug["sysCmdOn"]:
				t = threading.Timer(int(plug["sysCmdOnDelay"]),os.system,args=[plug["sysRunCmdOn"]])
				t.start()
	
	def turn_off(self, plugip):
		self._wemosmartplug_logger.debug("Turning off %s." % plugip)
		plug = self.plug_search(self._settings.get(["arrSmartplugs"]),"ip",plugip)
		self._wemosmartplug_logger.debug(plug)
		if plug["sysCmdOff"]:
			t = threading.Timer(int(plug["sysCmdOffDelay"]),os.system,args=[plug["sysRunCmdOff"]])
			t.start()			
		if plug["autoDisconnect"]:
			self._printer.disconnect()
			time.sleep(int(plug["autoDisconnectDelay"]))
		chk = self.sendCommand("off",plugip)
		if chk == 0:
			self.check_status(plugip)
		
	def check_status(self, plugip):
		self._wemosmartplug_logger.debug("Checking status of %s." % plugip)
                
		if plugip != "":
			chk = self.sendCommand("status",plugip)

			if chk == 1:
                                print("Setting state to on!")
				self._plugin_manager.send_plugin_message(self._identifier, dict(currentState="on",ip=plugip))
			elif chk == 0:
                                print("Setting state to off")
				self._plugin_manager.send_plugin_message(self._identifier, dict(currentState="off",ip=plugip))
			else:
				self._wemosmartplug_logger.debug(chk)
				self._plugin_manager.send_plugin_message(self._identifier, dict(currentState="unknown",ip=plugip))		
	
	def get_api_commands(self):
		return dict(turnOn=["ip"],turnOff=["ip"],checkStatus=["ip"])

	def on_api_command(self, command, data):
		if not user_permission.can():
			from flask import make_response
			return make_response("Insufficient rights", 403)
        
		if command == 'turnOn':
			self.turn_on("{ip}".format(**data))
		elif command == 'turnOff':
			self.turn_off("{ip}".format(**data))
		elif command == 'checkStatus':
			self.check_status("{ip}".format(**data))
			
	##~~ Utilities
	
	def plug_search(self, list, key, value): 
		for item in list: 
			if item[key] == value: 
				return item
                        else:
                                self._wemosmartplug_logger.info("No plug found matching %s, incorrect IP in GCODE ?" % ( key ))
                                return
	
	def sendCommand(self, cmd, plugip):	
		try:

			# this is done every time because the wemo changes port, randomly.. for some reason. 
			port = pywemo.ouimeaux_device.probe_wemo(plugip)
			url = 'http://%s:%i/setup.xml' % (plugip, port)
			device = pywemo.discovery.device_from_description(url, None)
                        ret = "unset"
			self._wemosmartplug_logger.info("Sending command %s to %s" % (cmd,plugip))

                        print("Device: %s" % (dir(device)))
            
                        if cmd == "status":
                                ret = device.get_state()
                        elif cmd == "off":
                                device.off()
                        elif cmd == "on":
                                # FIXME: this is kinda crappy.
                                device.on()


                        else:
                                print("COMMAND WAS: %s" % ( cmd))
                                ret = "unknown"

                        print("Command was; %s" % ( cmd ))
                        print("Return value: %s" % ( ret ))
			return json.loads('{"system":{"get_sysinfo":{"relay_state":3}}}')
                
		except socket.error:
			self._wemosmartplug_logger.debug("Could not connect to %s." % plugip)
                        return json.loads("{}")
			
	##~~ Gcode processing hook
	
	def gcode_turn_off(self, plug):
		if plug["warnPrinting"] and self._printer.is_printing():
			self._logger.info("Not powering off %s because printer is printing." % plug["label"])
		else:
			self.turn_off(plug["ip"])
	
	def processGCODE(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
		if gcode:
			if cmd.startswith("M80"):			
				plugip = re.sub(r'^M80\s?', '', cmd)
				self._wemosmartplug_logger.debug("Received M80 command, attempting power on of %s." % plugip)
				plug = self.plug_search(self._settings.get(["arrSmartplugs"]),"ip",plugip)
				if plug["gcodeEnabled"]:
					t = threading.Timer(int(plug["gcodeOnDelay"]),self.turn_on,args=[plugip])
					t.start()
				return
			elif cmd.startswith("M81"):
				plugip = re.sub(r'^M81\s?', '', cmd)
				self._wemosmartplug_logger.debug("Received M81 command, attempting power off of %s." % plugip)
				plug = self.plug_search(self._settings.get(["arrSmartplugs"]),"ip",plugip)
				self._wemosmartplug_logger.debug(plug)
				if plug["gcodeEnabled"]:
					t = threading.Timer(int(plug["gcodeOffDelay"]),self.gcode_turn_off,[plug])
					t.start()
				return
			else:
				return
			

	##~~ Softwareupdate hook

	def get_update_information(self):
		# Define the configuration for your plugin to use with the Software Update
		# Plugin here. See https://github.com/foosel/OctoPrint/wiki/Plugin:-Software-Update
		# for details.
		return dict(
			wemosmartplug=dict(
				displayName="Wemo Smartplug",
				displayVersion=self._plugin_version,

				# version check: github repository
				type="github_release",
				user="wmealing",
				repo="OctoPrint-WemoSmartPlug",
				current=self._plugin_version,

				# update method: pip
				pip="https://github.com/wmealing/OctoPrint-WemoSmartplug/archive/{target_version}.zip"
			)
		)


# If you want your plugin to be registered within OctoPrint under a different name than what you defined in setup.py
# ("OctoPrint-PluginSkeleton"), you may define that here. Same goes for the other metadata derived from setup.py that
# can be overwritten via __plugin_xyz__ control properties. See the documentation for that.
__plugin_name__ = "Wemo Smartplug"

def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = wemosmartplugPlugin()

	global __plugin_hooks__
	__plugin_hooks__ = {
		"octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.processGCODE,
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
	}


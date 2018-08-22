# *-* coding: utf-8 *-*
# nvdastore/__init__.py
#A part of the NVDAStore Add-on
#Copyright (C) 2017 Yannick PLASSIARD
#This file is covered by the GNU General Public License.
#See the file LICENSE for more details.



import os, sys, time, threading, Queue
import globalVars
import globalPluginHandler, logHandler
import api
import ui, wx, gui, core, config, nvwave
import storeGui, storeUtils, storeConfig
import capabilities
sys.path.append(os.path.dirname(__file__))
import hmac
import requests
import json
import storeApi
del sys.path[-1]
import addonHandler
addonHandler.initTranslation()

NVDASTORE_MODULE_NAME = 'nvdastore'
GP_CONNECTED = 0
GP_DISCONNECTED = 1


class NetworkChecker(threading.Thread):
    shouldTerminate = False
    def __init__(self, gpObject):
        self.gpObject = gpObject
        super(NetworkChecker, self).__init__()

    def run(self):
        self.timeDelay = 1.0
        self.lastTime = time.time()
        while self.shouldTerminate is False:
            if time.time() - self.lastTime > self.timeDelay:
                ret = None
                try:
                    ret = self.gpObject.storeClient.getModuleCategories()
                except:
                    logHandler.log.debugWarning("Failed to connect to the NVDA Store.")
                logHandler.log.debugWarning("Ping reply: {}".format(ret))
                if ret is None or len(ret) == 0:
                    self.timeDelay = 5.0
                    data = {}
                    data["type"] = GP_DISCONNECTED
                    data["message"] = _("Not connected to the NVDA Store")
                    with self.gpObject.queueLock:
                        self.gpObject.msgQueue.put(data)
                else:
                    self.timeDelay = 3600.0
                    data = {}
                    data["type"] = GP_CONNECTED
                    data["message"] = _("Connected to the NVDA Store")
                    with self.gpObject.queueLock:
                        self.gpObject.msgQueue.put(data)
                self.lastTime = time.time()
            time.sleep(1.0)
                
class StoreAddon(object):
    id = ""
    category = ""
    name = ""
    description = ""
    author = ""
    email = ""
    latestVersion = ""
    versionChangelog = ""
    versionId = ""

    def __init__(self, id, category, name, author, email, description):
        super(StoreAddon, self).__init__()
        self.id = id
        self.category = category
        self.name = name
        self.author = author
        self.email = email
        self.description = description
    def addVersion(self, id, version, changelog, minVersion, maxVersion, capabilities=None):
        import versionInfo
        if (versionInfo.version >= minVersion and versionInfo.version <= maxVersion) or 'next' in versionInfo.version or 'dev' in versionInfo.version or 'master' in versionInfo.version or 'rc' in versionInfo.version:
            if self.checkCapabilities(version, capabilities):
                self.latestVersion = version
                self.versionChangelog = "Version: " + version + "\r\n" + changelog + "\r\n\r\n" + self.versionChangelog
                self.versionId = id
    def __str__(self):
        return u"%s" %(self.name)
    def __repr__(self):
        return u"%s" %(self.name)
    

    def checkCapabilities(self, version, requiredCaps):
        global capCache
        missingCaps = []
        if requiredCaps is None:
            return True
        for capability in requiredCaps.split(","):
            try:
                ret = capCache[capability]
            except:
                ret = None
            if ret is not None:
                if ret is False:
                    missingCaps.append(capability)
                continue                
            capName = "cap_%s" % capability
            capMethod = getattr(capabilities, capName, None)
            if capMethod is not None:
                logHandler.log.info("Executing %s" % capName)
                try:
                    ret = capMethod()
                except Exception, e:
                    ret = False
                    logHandler.log.exception("Failed to execute method", e)
            else:
                ret = False
            capCache[capability] = ret
            if ret is False:
                missingCaps.append(capability)
        if len(missingCaps) > 0:
            logHandler.log.info("The following capabilities are missing for %s to be installed: %s" %(self.name, ", ".join(missingCaps)))
            return False
        return True

capCache={}
class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = _("NVDAStore")
    addons = []
    queueLock = threading.Lock()
    msgQueue = Queue.Queue()
    lastData = None
    
    def __init__(self):
        super(globalPluginHandler.GlobalPlugin, self).__init__()
        self.networkChecker = NetworkChecker(self)
        self.networkChecker.start()
        
        if globalVars.appArgs.secure: return
        self.prefsMenu = gui.mainFrame.sysTrayIcon.preferencesMenu
        # Instanciate our preference menu.
        self.storePrefs = self.prefsMenu.Append(wx.ID_ANY, _(u"NVDAStore settings..."), _(u"NVDAStore Add-on settings"))
        gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, storeConfig.onConfigDialog, self.storePrefs)
        
        self.refreshing = False
        self.updates = []
        self.storeClient = storeApi.NVDAStoreClient()
        wx.CallLater(1000, self.onTimer)

    def terminate(self):
        self.networkChecker.shouldTerminate = True
        self.networkCheck.join()
        

    def onTimer(self):
        try:
            with self.queueLock:
                data = self.msgQueue.get_nowait()
                self.lastData = data
                # logHandler.log.debugWarning("Got network checker message: {}".format(data))
                if 'message' in data:
                    logHandler.log.debugWarning(data["message"])
                if data["type"] is GP_CONNECTED:
                    self.refreshAddons()
        except:
            pass
        wx.CallLater(1000, self.onTimer)
        
    def getCategory(self, catList, id):
        for cat in catList:
            if cat[u'id'] == id:
                return cat[u'name']
        return None

    def doRefreshAddons(self):
        if self.refreshing is False:
            self.refreshing = True
            self.refreshAddons()

    def refreshAddons(self, silent=False):
        addonHandler.initTranslation()
        global capCache
        self.refreshing = True
        capCache = {}
        self.addons = []
        modules = self.storeClient.getNvdaModules()
        notifs = self.storeClient.getNotifications()
        if len(notifs) > 0:
            ui.message(_(u"Notification: %s" %(", ".join(notifs))))
        if modules is None or len(modules) == 0:
            ui.message(_("Unable to connect to the Cecitek NVDAStore. Please check you're connected to the internet."))
            return
        catList = self.storeClient.getModuleCategories()
        if catList is None or len(catList) == 0:
            ui.message(_("Unable to connect to the Cecitek NVDAStore. Please check you're connected to the internet."))
            return
        
        for module in modules:
            m = StoreAddon(module[u'id'], self.getCategory(catList, module[u'id_category']), module[u'name'], module[u'author'], module[u'email'], module[u'description'])
            for v in module[u'versions']:
                caps = None
                try:
                    caps = v[u'capabilities']
                except:
                    caps = None
                m.addVersion(v[u'id'], v[u'version'], v[u'changelog'], v[u'minNvdaVersion'], v[u'maxNvdaVersion'], caps)
            if m.latestVersion != "":
                self.addons.append(m)
            
        log = ""
        for a in self.addons:
            log += "%s (%s) " %(a.name, a.latestVersion)
        logHandler.log.info("Available addons in the store: %s" % log)
        self.refreshing = False
        self.selfUpdate(silent)

    def getLocalAddon(self, storeAddon):
        for a in addonHandler.getAvailableAddons():
            if a.manifest['name'].upper() == storeAddon.name.upper():
                return a
        return None

    def selfUpdate(self, silent=False):
        addonHandler.initTranslation()
        self.updates = []
        for addon in self.addons:
            localAddon = self.getLocalAddon(addon)
            if localAddon and localAddon.manifest[u'version'] < addon.latestVersion:
                if addon.name.upper() == NVDASTORE_MODULE_NAME.upper():
                    # We should self-update the NVDAStore module itself.
                    if gui.messageBox(_(u"A new release is available for the NVDAStore add-on. Woul,d you like to install it right now? This will cause NVDA to restart."),
		                      _(u"Update available"),
                                      wx.YES_NO | wx.ICON_WARNING) == wx.YES:
                        ui.message(_("Updating..."))
                        ret = storeUtils.installAddon(self.storeClient, addon, True, True)
                        if ret: return
                    else:
                        break
                else:
                    self.updates.append(addon)
        if len(self.updates) > 0:
            nvwave.playWaveFile(os.path.join(os.path.dirname(__file__), "..", "..", "sounds", "notify.wav"))
            if silent is False:
                ui.message(_(u"Addons updates available."))


    def script_updateAll(self, gesture):
        if self.lastData is None or self.lastData["type"] is GP_DISCONNECTED:
            ui.message(self.lastData["message"])
            return
        self.updateAll()
    script_updateAll.__doc__ = _(u"Updates all addons to the latest version")

    def updateAll(self):
        updated = 0
        if len(self.updates) is 0:
            self.refreshAddons(True)
            if len(self.updates) == 0:
                ui.message(_(u"NVDAStore: No update available."))
                return
            
                           
        for update in self.updates:
            gui.mainFrame.prePopup()
            progressDialog = gui.IndeterminateProgressDialog(gui.mainFrame,
			                                     _("NVDAStore"),
			                                     _(u"Updating {name}...".format(name=update.name)))
            ui.message(_("Updating {name}".format(name=update.name)))
            try:
                gui.ExecAndPump(storeUtils.installAddon, self.storeClient, update, False, True)
            except:
                progressDialog.done()
                del progressDialog
                gui.mainFrame.postPopup()
                break
            progressDialog.done()
            del progressDialog
            updated += 1
            gui.mainFrame.postPopup()
        if updated:
            core.restart()

    def script_nvdaStore(self, gesture):
        if self.lastData is None or self.lastData["type"] is GP_DISCONNECTED:
            ui.message(self.lastData["message"])
            return
        gui.mainFrame.prePopup()
        progressDialog = gui.IndeterminateProgressDialog(gui.mainFrame,
			                                 _("Updating addons' list"),
			                                 _("Please wait while the add-on list is being updated."))
        try:
            gui.ExecAndPump(self.doRefreshAddons)
        except:
            progressDialog.done()
            del progressDialog
            gui.mainFrame.postPopup()
            return
        progressDialog.done()
        del progressDialog
        gui.mainFrame.postPopup()
        
        
        gui.mainFrame.prePopup()
        dlg = storeGui.StoreDialog(gui.mainFrame, self.storeClient, self.addons)
        dlg.Show()
        gui.mainFrame.postPopup()
        del dlg

    script_nvdaStore.__doc__ = _("Opens the NVDA Store to download, install and update NVDA add-ons.")

    __gestures = {
        "kb:nvda+shift+control+n": "nvdaStore",
        "kb:nvda+shift+control+u": "updateAll",
    }

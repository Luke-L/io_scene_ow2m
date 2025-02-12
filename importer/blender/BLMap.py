import enum
import bpy
from mathutils import Quaternion

from . import BLUtils
from . import BLEntity
from . import BLModel
from .BLMaterial import BlenderMaterialTree
from ...readers import PathUtil
from ...textureMap import TextureTypes


class QueueItem:
    def __init__(self, parent, rec):
        self.parent = parent
        self.rec = rec


class BlenderTree:
    def __init__(self, joinMeshes=False):
        self.cloneQueue = {}
        self.parentChildren = {}
        self.linkQueue = {}
        self.removeQueue = set()
        self.joinMeshes = joinMeshes

    def addQueueRoot(self, col):
        self.cloneQueue.setdefault(col, {})
        self.linkQueue.setdefault(col, set())

    def queueClone(self, obj, parent, col, rec):
        self.cloneQueue[col].setdefault(obj, set())
        self.cloneQueue[col][obj].add(QueueItem(parent, rec))

    def queueLink(self, obj, col):
        self.linkQueue[col].add(obj)

    def queueLinkRecursive(self, obj, col):
        self.linkQueue[col].add(obj)
        for child in self.parentChildren.get(obj.name, []):
            self.queueLinkRecursive(child,col)
    
    def queueRemove(self, obj, deep=False):
        self.removeQueue.add(obj)
        if deep and obj.data:
            self.removeQueue.add(obj.data)

    def queueRemoveRecursive(self, obj, deep=False):
        self.queueRemove(obj, deep)
        for child in self.parentChildren.get(obj.name, []):
            self.queueRemove(child, deep)

    def removeRecursive(self, obj, deep=False):
        t = set()
        t.add(obj)
        for child in self.parentChildren.get(obj.name, []): # TODO change this?
           t.add(child)
           if child.data:
            t.add(child.data)
        bpy.data.batch_remove(t)

    def parent(self, obj, parent):
        obj.parent = parent
        self.parentChildren.setdefault(parent.name, [])
        self.parentChildren[parent.name].append(obj)

    def startQueues(self):
        print("[owm]: Copying objects")
        for col in self.cloneQueue:
            for obj in self.cloneQueue[col]:
                for instance in self.cloneQueue[col][obj]:
                    self.recursiveCopy(obj, instance.parent, False, col, instance.rec)

        #bpy.data.batch_remove(self.removeQueue)
        #bpy.data.batch_remove(matTree.unusedMaterials)
        objs = 0
        for col in self.linkQueue:
            objs+=len(self.linkQueue[col])

        print("[owm]: Linking {} objects".format(objs))
        for col in self.linkQueue:
            for obj in self.linkQueue[col]:
                col.objects.link(obj)

    def createModelHierarchy(self, model, name):
        if self.joinMeshes:
            rootFolder = model.armature if model.armature else model.meshes[0]
        else:
            rootFolder = model.armature if model.armature else BLUtils.createFolder(name, False)
        self.parentChildren.setdefault(rootFolder.name, [])

        for mesh in model.meshes:
            self.parent(mesh, rootFolder)
            

        # Parent and link hardpoints
        if model.empties[0] is not None:  # this will be none if importEmpties is false
            self.parent(model.empties[0], rootFolder)

            for emptyObj in model.empties[1].values():
                self.parent(emptyObj, model.empties[0])

                # retarget to armature
                if len(emptyObj.constraints) > 0:
                    emptyObj.constraints[0].targets[0].target = rootFolder
        return rootFolder

    def createEntityHierarchy(self, entity, name):
        # print(entity)
        if len(entity.children) > 0:
            rootFolder = BLUtils.createFolder(name)
            if entity.baseModel:
                self.parent(self.createModelHierarchy(entity.baseModel, entity.baseModel.meshData.header.name), rootFolder)
            for child in entity.children:
                childFolder = self.createEntityHierarchy(child, child.name)
                if childFolder:
                    self.parent(childFolder, rootFolder)
            return rootFolder
        else:
            if entity.baseModel:
                return self.createModelHierarchy(entity.baseModel, name)
        return None

    def recursiveCopy(self, obj, parent, original=True, col=None, rec=None):
        new_obj = obj.copy()
        if obj.data is not None:
            if original is True:
                new_obj.data = obj.data.copy()

            # Retarget mesh armature modifiers
            if obj.get('owm.mesh.armature', 0) == 1:
                mod = new_obj.modifiers['OWM Skeleton']
                mod.object = parent

        # Set transforms        
        if rec:
            self.applyRec(new_obj, rec)

        # Retarget hardpoint constraints
        if "Armature" in new_obj.constraints:
            new_obj.constraints[0].targets[0].target = parent.parent # Sockets folder parent

        new_obj.parent = parent

        if original:
            self.parentChildren[new_obj.name] = set()
            #self.queueRemove(obj, True)
            #self.queueRemove(new_obj)

        else:
            self.queueLink(new_obj, col)

        if obj.type != "MESH":
            for child in self.parentChildren.get(obj.name, []):
                new_child = self.recursiveCopy(child, new_obj, original, col) #TODO maybe change this to references
                if original:
                    self.parentChildren[new_obj.name].add(new_child)
        return new_obj

    def applyRec(self, obj, rec, queueLink=False, col=None):
        obj.location = BLUtils.pos_matrix(rec.position)
        obj.rotation_euler = Quaternion(BLUtils.wxzy(rec.rotation)).to_euler('XYZ')
        obj.scale = BLUtils.xpzy(rec.scale)
        if queueLink:
            self.queueLinkRecursive(obj, col)


collisionMats = TextureTypes["CollisionMaterialLooks"]

def progress_bar(op, current, total, bar_length=20): #TODO find a place for this ig
    fraction = current / total

    arrow = int(fraction * bar_length - 1) * '-' + '>'
    padding = int(bar_length - len(arrow)) * ' '

    ending = '\n' if current == total    else '\r'

    print(f'{op}: [{arrow}{padding}] {int(fraction*100)}%', end=ending)

def init(mapTree, mapName, mapRootPath, mapSettings, modelSettings, entitySettings):
    blenderTree = BlenderTree(mapSettings.joinMeshes)
    matTree = BlenderMaterialTree(mapTree.modelLookPaths) if modelSettings.importMaterial else None
    sceneCol = bpy.context.view_layer.active_layer_collection.collection
    rootMapCol = bpy.data.collections.new(mapName)
    sceneCol.children.link(rootMapCol)

    if mapSettings.importObjects:
        objectsCol = bpy.data.collections.new(mapName + '_OBJECTS')

    if mapSettings.importDetails:
        detailsCol = bpy.data.collections.new(mapName + '_DETAILS')

    models = len(mapTree.objects)-1
    for i,objID in enumerate(mapTree.objects):
        progress_bar("[owm]: Loading models",i,models)
        # create a "folder" for this model
        objFolder = None
        isEntity = mapTree.modelFilepaths[objID].endswith(".owentity")

        if isEntity:  # this is so bad ngl also TODO move this path sih
            objModel = BLEntity.readEntity(PathUtil.makePathAbsolute(mapRootPath, mapTree.modelFilepaths[objID]), modelSettings, entitySettings)
            modelFolder = blenderTree.createEntityHierarchy(objModel, objID)
            if modelFolder is None:
                continue
        else:
            objModel = BLModel.readMDL(mapTree.modelFilepaths[objID], modelSettings)
            modelFolder = blenderTree.createModelHierarchy(objModel, objID)

        for objLookID in mapTree.objects[objID]:
            if mapSettings.removeCollision and objLookID in collisionMats:
                continue
            elif objFolder is None:
                objFolder = bpy.data.collections.new(objID + '_COLLECTION')
                blenderTree.addQueueRoot(objFolder)
                objCol = objectsCol if objID not in mapTree.details else detailsCol
                objCol.children.link(objFolder)

            if modelSettings.importMaterial:
                # create a "folder" for the material
                lookFolder = BLUtils.createFolder(objLookID if objLookID else "null" + '_LOOK')  # maybe make this a collection
                objFolder.objects.link(lookFolder)

                if objLookID:
                    if isEntity:
                        matTree.bindEntityLook(objModel, objLookID)
                    else:
                        #print("Binding material look {} to model group {}".format(objLookID, objID))
                        matTree.bindModelLook(objModel, objLookID)

                lookModel = blenderTree.recursiveCopy(modelFolder, lookFolder, True, objFolder)

                for i, rec in enumerate(mapTree.objects[objID][objLookID]):
                    if i == 0:
                        blenderTree.applyRec(lookModel, rec, True, objFolder)
                        continue
                    blenderTree.queueClone(lookModel, lookFolder, objFolder, rec)
            else:
                for i, rec in enumerate(mapTree.objects[objID][objLookID]):
                    if i == 0:
                        blenderTree.applyRec(modelFolder, rec, True, objFolder)
                        continue
                    blenderTree.queueClone(modelFolder, None, objFolder, rec)

        blenderTree.queueRemoveRecursive(modelFolder)

    #bpy.data.batch_remove(blenderTree.removeQueue)
    if modelSettings.importMaterial:
        matTree.removeSkeletonNodeTrees()
        
    blenderTree.startQueues()

    if mapSettings.importObjects:
        rootMapCol.children.link(objectsCol)

    if mapSettings.importDetails:
        rootMapCol.children.link(detailsCol)

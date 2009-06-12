import os
import tarfile
import md5
import tempfile
import types
import re
from DIRAC.Core.DISET.TransferClient import TransferClient
from DIRAC.Core.DISET.RPCClient import RPCClient
from DIRAC.DataManagementSystem.Client.ReplicaManager import ReplicaManager
from DIRAC.Core.Utilities.File import getSize, getGlobbedTotalSize
from DIRAC.Core.Utilities import List
from DIRAC import gLogger, S_OK, S_ERROR, gConfig

class SandboxStoreClient:

  __validSandboxTypes = ( 'Input', 'Output' )

  def __init__( self ):
    self.__serviceName = "WorkloadManagement/SandboxStore"

  def __getRPCClient( self ):
    return RPCClient( self.__serviceName )

  def __getTransferClient( self ):
    return TransferClient( self.__serviceName )

  #Upload sandbox to jobs and pilots

  def uploadFilesAsSandboxForJob( self, fileList, jobId, sbType, sizeLimit = 0 ):
    if sbType not in self.__validSandboxTypes:
      return S_ERROR( "Invalid Sandbox type %s" % sbType )
    return self.uploadFilesAsSandbox( fileList, sizeLimit, assignTo = { "Job:%s" % jobId: sbType } )

  def uploadFilesAsSandboxForPilot( self, fileList, jobId, sbType, sizeLimit = 0 ):
    if sbType not in self.__validSandboxTypes:
      return S_ERROR( "Invalid Sandbox type %s" % sbType )
    return self.uploadFilesAsSandbox( fileList, sizeLimit, assignTo = { "Pilot:%s" % jobId: sbType } )

  #Upload generic sandbox

  def uploadFilesAsSandbox( self, fileList, sizeLimit = 0, assignTo = {} ):
    """ Send files in the fileList to a Sandbox service for the given jobID.
        This is the preferable method to upload sandboxes. fileList can contain
        both files and directories
        Parameters:
          - assignTo : Dict containing { 'Job:<jobid>' : '<sbType>', ... }
    """
    errorFiles = []
    files2Upload = []
    
    for key in assignTo:
      if assignTo[ key ] not in self.__validSandboxTypes:
        return S_ERROR( "Invalid sandbox type %s" % assignTo[ key ] )

    if type( fileList ) not in ( types.TupleType, types.ListType ):
      return S_ERROR( "fileList must be a tuple!" )

    for file in fileList:
      if re.search( '^lfn:', file ) or re.search( '^LFN:', file ):
        pass
      else:
        if os.path.exists( file ):
          files2Upload.append( file )
        else:
          errorFiles.append( file )

    if errorFiles:
      return S_ERROR( "Failed to locate files: %s" % ", ".join( errorFiles ) )

    try:
      fd, tmpFilePath = tempfile.mkstemp( prefix="LDSB." )
    except Exception, e:
      return S_ERROR( "Cannot create temporal file: %s" % str(e) )

    tf = tarfile.open( name = tmpFilePath, mode = "w|bz2" )
    for file in files2Upload:
      tf.add( os.path.realpath( file ), os.path.basename( file ), recursive = True )
    tf.close()

    if sizeLimit > 0:
      # Evaluate the compressed size of the sandbox
      if getGlobbedTotalSize( tmpFilePath ) > sizeLimit:
        result = S_ERROR( "Size over the limit" )
        result[ 'SandboxFileName' ] = tmpFilePath
        return result

    oMD5 = md5.new()
    fd = open( tmpFilePath, "rb" )
    bData = fd.read( 10240 )
    while bData:
      oMD5.update( bData )
      bData = fd.read( 10240 )
    fd.close()

    transferClient = self.__getTransferClient()
    result = transferClient.sendFile( tmpFilePath, ( "%s.tar.bz2" % oMD5.hexdigest(), assignTo ) )
    try:
      os.unlink( tmpFilePath )
    except:
      pass
    return result

  ##############
  # Download sandbox

  def downloadSandbox( self,  sbLocation,  destinationDir=""  ):
    """
    Download a sandbox file and keep it in bundled form
    """
    if sbLocation.find( "SB:" ) != 0:
      return S_ERROR( "Invalid sandbox URL" )
    sbLocation = sbLocation[ 3: ]
    sbSplit = sbLocation.split( "|" )
    if len( sbSplit ) < 2:
      return S_ERROR( "Invalid sandbox URL" )
    SEName = sbSplit[0]
    SEPFN = "|".join( sbSplit[1:] )
    #If destination dir is not specified use current working dir
    #If its defined ensure the dir structure is there
    if not destinationPath:
      destinationPath = os.getcwd()
    else:
      try:
        os.makedirs( destinationDir )
      except:
        pass

    try:
      tmpSBDir = tempfile.mkdtemp( prefix="TMSB." )
    except Exception, e:
      return S_ERROR( "Cannot create temporal file: %s" % str(e) )

    rm = ReplicaManager()
    result = rm.getPhysicalFile( SEPFN, SEName, tmpSBDir, singleFile = True )
    if not result[ 'OK' ]:
      return result
    sbFileName = os.path.basename( SEPFN )

    result = S_OK()
    tarFileName = os.path.join( tmpSBDir, sbFileName )
    try:
      tf = tarfile.open( name = tarFileName, mode = "r" )
      for tarinfo in tf:
        tf.extract( tarinfo, path = destinationDir )
      tf.close()
    except Exception, e:
      result = S_ERROR( "Could not open bundle: %s" % str(e) )

    try:
      os.unlink( tarFileName )
      os.rmdir( tmpSBDir )
    except Exception, e:
      gLogger.warn( "Could not remove temporary dir %s: %s" % ( tmpSBDir, str(e) ) )

    return result

  ##############
  # Jobs

  def getSandboxesForJob( self, jobId ):
    return self.__getSandboxesForEntity( "Job:%s" % jobId )

  def assignSandboxesToJob( self, jobId, sbList ):
    return self.__assignSandboxesToEntity( "Job:%s" % jobId, sbList )

  def assignSandboxToJob( self, jobId, sbLocation, sbType ):
    return self.__assignSandboxToEntity( "Job:%s" % jobId, sbLocation, sbType )

  def unassignJobs( self, jobIdList ):
    if type( jobIdList ) in ( types.IntType, types.LongType ):
      jobIdList = [ jobIdList ]
    entitiesList = []
    for jobId in jobIdList:
      entitiesList.append( "Job:%s" % jobId )
    return self.__unassignEntities( entitiesList )

  def downloadSandboxForJob( self, jobId, sbType, destinationPath="" ):
    result = self.__getSandboxesForEntity( "Job:%s" % jobId )
    if not result[ 'OK' ]:
      return result
    sbDict = result[ 'Value' ]
    if sbType not in sbDict:
      return S_ERROR( "No %s sandbox registered for job %s" % ( sbType, jobId ) )
    for sbLocation in sbDict[ sbType ]:
      result = self.downloadSandbox( sbLocation, destinationPath )
      if not result[ 'OK' ]:
        return result
    return S_OK()

  ##############
  # Pilots

  def getSandboxesForPilot( self, pilotId ):
    return self.__getSandboxesForEntity( "Pilot:%s" % pilotId )

  def assignSandboxesToPilot( self, pilotId, sbList ):
    return self.__assignSandboxesToEntity( "Pilot:%s" % pilotId, sbList )

  def assignSandboxToPilot( self, pilotId, sbLocation, sbType ):
    return self.__assignSandboxToEntity( "Pilot:%s" % pilotId, sbLocation, sbType )

  def unassignPilots( self, pilotIdIdList ):
    if type( pilotIdIdList ) in ( types.IntType, types.LongType ):
      pilotIdIdList = [ pilotIdIdList ]
    entitiesList = []
    for pilotId in pilotIdIdList:
      entitiesList.append( "Pilot:%s" % pilotId )
    return self.__unassignEntities( entitiesList )

  def downloadSandboxForPilot( self, jobId, sbType, destinationPath="" ):
    result = self.__getSandboxesForEntity( "Pilot:%s" % jobId )
    if not result[ 'OK' ]:
      return result
    sbDict = result[ 'Value' ]
    if sbType not in sbDict:
      return S_ERROR( "No %s sandbox registered for pilot %s" % ( sbType, jobId ) )
    for sbLocation in sbDict[ sbType ]:
      result = self.downloadSandbox( sbLocation, destinationPath )
      if not result[ 'OK' ]:
        return result
    return S_OK()

  ##############
  # Entities

  def __getSandboxesForEntity( self, eId ):
    """
    Get the sandboxes assigned to jobs and the relation type
    """
    return self.__getRPCClient().getSandboxesAssignedToEntity( eId )

  def __assignSandboxesToEntity( self, eId, sbList ):
    """
    Assign sandboxes to a job.
    sbList must be a list of sandboxes and relation types
      sbList = [ ( "SB:SEName|SEPFN", "Input" ), ( "SB:SEName|SEPFN", "Output" ) ]
    """
    for sbT in sbList:
      if sbT[1] not in self.__validSandboxTypes:
        return S_ERROR( "Invalid Sandbox type %s" % sbT[1] )
    return self.__getRPCClient().assignSandboxesToEntities( { eId : sbList } )

  def __assignSandboxToEntity( self, eId, sbLocation, sbType ):
    """
    Assign a sandbox to a job
      sbLocation is "SEName:SEPFN"
      sbType is Input or Output
    """
    return self.__assignSandboxesToEntity( eId, [ ( sbLocation, sbType ) ] )

  def __unassignEntities( self, eIdList ):
    """
    Unassign a list of jobs of their respective sandboxes
    """
    return self.__getRPCClient().unassignEntities( eIdList )

  #TODO: DELETEME WHEn OLD SANDBOXES ARE REMOVED
  def useOldSandboxes( self, prefix = "" ):
    if prefix:
      prefix="%s-" % prefix
    setup = gConfig.getValue( "/DIRAC/Setup", "Default" )
    return gConfig.getValue( "/DIRAC/%s%s-UseOldSandboxes" % ( prefix, setup ), True )
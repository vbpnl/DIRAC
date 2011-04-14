"""
The PDP (Policy Decision Point) module is used to:

1. Decides which policies have to be applied.

2. Invokes an evaluation of the policies, and returns the result (to a PEP)
"""
#############################################################################

#import time
import datetime
import copy
import sys

from DIRAC.ResourceStatusSystem.Utilities.Utils import where, assignOrRaise
from DIRAC.ResourceStatusSystem.Utilities.Utils import ValidRes, ValidStatus, ValidSiteType, ValidServiceType, ValidResourceType
from DIRAC.ResourceStatusSystem.Utilities.Exceptions import InvalidRes, InvalidStatus, InvalidSiteType, InvalidServiceType, InvalidResourceType, RSSException
from DIRAC.ResourceStatusSystem.Utilities.InfoGetter import InfoGetter
from DIRAC.ResourceStatusSystem.PolicySystem.PolicyCaller import PolicyCaller
from DIRAC.ResourceStatusSystem.Command.CommandCaller import CommandCaller

#############################################################################


class PDP:
  """
  PDP = Policy Decision Point.

  Used to invoke policies and to take decision based on the polict results combination.
  """

#############################################################################

  def __init__(self, VOExtension, granularity = None, name = None, status = None, formerStatus = None,
               reason = None, siteType = None, serviceType = None, resourceType = None,
               useNewRes = False):
    """
    PDP (Policy Decision Point) initialization

    :params:
      :attr:`VOExtension`: string - VO extension (e.g. 'LHCb')

      :attr:`granularity`: string - a ValidRes

      :attr:`name`: string - name (e.g. of a site)

      :attr:`status`: string - status

      :attr:`formerStatus`: string - former status

      :attr:`reason`: string - optional reason for last status change

      :attr:`siteType`: string - optional site type

      :attr:`serviceType`: string - optional service type

      :attr:`resourceType`: string - optional resource type
    """

    self.VOExtension = VOExtension

    self.__granularity  = assignOrRaise(granularity, ValidRes, InvalidRes, self, self.__init__)
    self.__name         = name
    self.__status       = assignOrRaise(status, ValidStatus, InvalidStatus, self, self.__init__)
    self.__formerStatus = assignOrRaise(formerStatus, ValidStatus, InvalidStatus, self, self.__init__)
    self.__reason       = reason
    self.__siteType     = assignOrRaise(siteType, ValidSiteType, InvalidSiteType, self, self.__init__)
    self.__serviceType  = assignOrRaise(serviceType, ValidServiceType, InvalidServiceType, self, self.__init__)
    self.__resourceType = assignOrRaise(resourceType, ValidResourceType, InvalidResourceType, self, self.__init__)

    cc = CommandCaller()
    self.pc = PolicyCaller(cc)

    self.useNewRes = useNewRes

    self.args      = None
    self.policy    = None
    self.knownInfo = None
    self.ig        = None


#############################################################################

  def takeDecision(self, policyIn=None, argsIn=None, knownInfo=None):
    """ PDP MAIN FUNCTION

        decides policies that have to be applied, based on

        __granularity,

        __name,

        __status,

        __formerStatus

        __reason

        If more than one policy is evaluated, results are combined.

        Logic for combination: a conservative approach is followed
        (i.e. if a site should be banned for at least one policy, that's what is returned)

        returns:

          { 'PolicyType': a policyType (in a string),
            'Action': True|False,
            'Status': 'Active'|'Probing'|'Banned',
            'Reason': a reason
            'EndDate: datetime.datetime (in a string)}
    """

    self.args = argsIn
    self.policy = policyIn
    self.knownInfo = knownInfo


    self.ig = InfoGetter(self.VOExtension)

    EVAL = self.ig.getInfoToApply(('policy', 'policyType'),
                                  granularity = self.__granularity,
                                  status = self.__status,
                                  formerStatus = self.__formerStatus,
                                  siteType = self.__siteType,
                                  serviceType = self.__serviceType,
                                  resourceType = self.__resourceType,
                                  useNewRes = self.useNewRes)

    policyCombinedResultsList = []

    for policyGroup in EVAL:

      policyType = policyGroup['PolicyType']

      if self.policy is not None:
        #TO CHECK!
        singlePolicyResults = self.policy.evaluate(self.policy)
      else:
        if policyGroup['Policies'] is None:
          return {'SinglePolicyResults' : [],
                  'PolicyCombinedResult' : [{'PolicyType': policyType,
                                             'Action': False,
                                             'Reason':'No policy results'}]}

        singlePolicyResults = self._invocation(self.VOExtension, self.__granularity,
                                               self.__name, self.__status, self.policy,
                                               self.args, policyGroup['Policies'])

      policyCombinedResults = self._policyCombination(singlePolicyResults)

      # policy results communication

      if policyCombinedResults['SAT']:
        newstatus = policyCombinedResults['Status']
        reason = policyCombinedResults['Reason']
        newPolicyType = self.ig.getNewPolicyType(self.__granularity, newstatus)
        for npt in newPolicyType:
          if npt not in policyType:
            policyType.append(npt)
        decision = {'PolicyType': policyType, 'Action': True, 'Status':'%s'%newstatus,
                    'Reason':'%s'%reason}
        if policyCombinedResults.has_key('EndDate'):
          decision['EndDate'] = policyCombinedResults['EndDate']
        policyCombinedResultsList.append(decision)

      elif not policyCombinedResults['SAT']:
        reason = policyCombinedResults['Reason']
        decision = {'PolicyType': policyType, 'Action': False, 'Reason':'%s'%reason}
        if policyCombinedResults.has_key('EndDate'):
          decision['EndDate'] = policyCombinedResults['EndDate']
        policyCombinedResultsList.append(decision)

    res = {'SinglePolicyResults': singlePolicyResults,
           'PolicyCombinedResult' : policyCombinedResultsList}

    return res

#############################################################################

  def _invocation(self, VOExtension, granularity, name, status, policy, args, policies):
    """ One by one, use the PolicyCaller to invoke the policies, and putting
        their results in `policyResults`. When the status is `Unknown`, invokes
        `self.__useOldPolicyRes`.
    """

    policyResults = []

    for p in policies:
      pName = p['Name']
      pModule = p['Module']
      extraArgs = p['args']
      commandIn = p['commandIn']
      res = self.pc.policyInvocation(VOExtension, granularity = granularity, name = name,
                                     status = status, policy = policy, args = args, pName = pName,
                                     pModule = pModule, extraArgs = extraArgs, commandIn = commandIn)

      if res['SAT'] == 'Unknown':
        res = self.__useOldPolicyRes(name = name, policyName = pName,
                                     status = status)

      if res['SAT'] == 'NeedConfirmation':
        pName = p['ConfirmationPolicy']
        triggeredPolicy = self.ig.C_Policies[pName]
        pModule = triggeredPolicy['module']
        extraArgs = triggeredPolicy['args']
        commandIn = triggeredPolicy['commandIn']
        res = self.pc.policyInvocation(VOExtension, granularity = granularity, name = name,
                                       status = status, policy = policy, args = args, pName = pName,
                                       pModule = pModule, extraArgs = extraArgs, commandIn = commandIn)

      if res['SAT'] != None:
        policyResults.append(res)

    return policyResults

#############################################################################

  def _policyCombination(self, policies):

    def combine(p1, p2):
      # TODO: Implement proper combination function elsewhere
      if ValidStatus.index(p1['Status']) > ValidStatus.index(p2['Status']):
        return p1
      elif ValidStatus.index(p1['Status']) < ValidStatus.index(p2['Status']):
        return p2
      else: return None

    def composeReason(r1, r2):
      if    r1 == '': return r2
      elif  r2 == '': return r1
      elif  r1 == r2: return r1
      else:           return r1 + ' |###| ' + r2

    def reducefun(p1, p2):

      res = {}

      # Endkey
      if p1.has_key('EndDate'):
        res['EndDate'] = p1['EndDate']
      elif p2.has_key('EndDate'):
        res['EndDate'] = p2['EndDate']

      if p1['SAT'] and p2['SAT']:        # Both are SAT
        comb = combine(p1, p2)
        if comb == None:
                        res           = copy.deepcopy(p1)
                        res['Reason'] = composeReason(p1['Reason'], p2['Reason'])
        else:           res           = comb
      elif p1['SAT'] or p2['SAT']:       # Only one is SAT
        comb = combine(p1, p2)
        if comb == None:
          if p1['SAT']: res = p1
          else:         res = p2
        else:           res = comb
      else:                              # Both are not SAT
        comb = combine(p1, p2)
        if comb == None: res = p1
        else:            res = comb
        res['Reason'] = composeReason(p1['Reason'], p2['Reason'])

      return res

    if sys.version_info[0] == 3:
      import functools
      res = functools.reduce(reducefun, policies, \
            {'SAT': False, 'Status': 'Active', 'Reason': ''})
    else:
      res = reduce(reducefun, policies, \
            {'SAT': False, 'Status': 'Active', 'Reason': ''})

    return copy.deepcopy(res)

#############################################################################

  def __useOldPolicyRes(self, name, policyName, status):
    """ Use the RSS Service to get an old policy result.
        If such result is older than 2 hours, it returns {'SAT':None}
    """

    from DIRAC.Core.DISET.RPCClient import RPCClient
    rsS = RPCClient("ResourceStatus/ResourceManagement")

    res = rsS.getPolicyRes(name, policyName, True)
    if not res['OK']:
      raise RSSException, where(self, self.__useOldPolicyRes) + ' Could not get a policy result'

    res = res['Value']

    if res == []:
      return {'SAT':None}

    oldStatus = res[0]
    oldReason = res[1]
    lastCheckTime = res[2]

    if ( lastCheckTime + datetime.timedelta(hours = 2) ) < datetime.datetime.utcnow():
      return {'SAT':None}

    result = {}

    if status == oldStatus:
      result['SAT'] = False
    else:
      result['SAT'] = True

    result['Status'] = oldStatus
    result['Reason'] = oldReason
    result['OLD'] = True
    result['PolicyName'] = policyName

    return result

#############################################################################

""" Create and init Axon, whcih services Forward and Backward requests from other neurons.
"""
# The MIT License (MIT)
# Copyright © 2021 Yuma Rao

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated 
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation 
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, 
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of 
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL 
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION 
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER 
# DEALINGS IN THE SOFTWARE.

import argparse
import os
import copy
import inspect
import time
from concurrent import futures
from typing import List, Callable
from bittensor._threadpool import prioritythreadpool

import torch
import grpc
from substrateinterface import Keypair

import bittensor
from . import axon_impl

class axon:
    """ The factor class for bittensor.Axon object
    The Axon acts a grpc server for the bittensor network and allows for communication between neurons.
    By default, the grpc server follows the bittensor protocol and transports forward and backwards requests
    between validators and servers. 
    
    Examples:: 
            >>> axon = bittensor.axon(config=config)
            >>> subtensor = bittensor.subtensor(network='nakamoto')
            >>> axon.serve(subtensor=subtensor)
    """

    def __new__(
            cls, 
            config: 'bittensor.config' = None,
            wallet: 'bittensor.Wallet' = None,
            forward_text: 'Callable' = None,
            backward_text: 'Callable' = None,
            synapse_last_hidden: 'Callable' = None,
            synapse_causal_lm: 'Callable' = None,
            synapse_causal_lm_next: 'Callable' = None,
            synapse_seq_2_seq: 'Callable' = None,
            synapse_checks: 'Callable' = None,
            thread_pool: 'futures.ThreadPoolExecutor' = None,
            server: 'grpc._Server' = None,
            port: int = None,
            ip: str = None,
            max_workers: int = None, 
            maximum_concurrent_rpcs: int = None,
            blacklist: 'Callable' = None,
            priority: 'Callable' = None,
            forward_timeout: int = None,
            backward_timeout: int = None,
            compression: str = None,
        ) -> 'bittensor.Axon':
        r""" Creates a new bittensor.Axon object from passed arguments.
            Args:
                config (:obj:`bittensor.Config`, `optional`): 
                    bittensor.axon.config()
                wallet (:obj:`bittensor.Wallet`, `optional`):
                    bittensor wallet with hotkey and coldkeypub.
                forward_text (:obj:`callable`, `optional`):
                    function which is called on forward text requests.
                backward_text (:obj:`callable`, `optional`):
                    function which is called on backward text requests.
                synapse_last_hidden (:obj:`callable`, `optional`):
                    function which is called by the last hidden synapse
                synapse_causal_lm (:obj:`callable`, `optional`):
                    function which is called by the causal lm synapse
                synapse_causal_lm_next (:obj:`callable`, `optional`):
                    function which is called by the TextCausalLMNext synapse
                synapse_seq_2_seq (:obj:`callable`, `optional`):
                    function which is called by the seq2seq synapse   
                synapse_checks (:obj:`callable`, 'optional'):
                    function which is called before each synapse to check for stake        
                thread_pool (:obj:`ThreadPoolExecutor`, `optional`):
                    Threadpool used for processing server queries.
                server (:obj:`grpc._Server`, `required`):
                    Grpc server endpoint, overrides passed threadpool.
                port (:type:`int`, `optional`):
                    Binding port.
                ip (:type:`str`, `optional`):
                    Binding ip.
                max_workers (:type:`int`, `optional`):
                    Used to create the threadpool if not passed, specifies the number of active threads servicing requests.
                maximum_concurrent_rpcs (:type:`int`, `optional`):
                    Maximum allowed concurrently processed RPCs.
                blacklist (:obj:`callable`, `optional`):
                    function to blacklist requests.
                priority (:obj:`callable`, `optional`):
                    function to assign priority on requests.
                forward_timeout (:type:`int`, `optional`):
                    timeout on the forward requests. 
                backward_timeout (:type:`int`, `optional`):
                    timeout on the backward requests.              
        """   

        if config == None: 
            config = axon.config()
        config = copy.deepcopy(config)
        config.axon.port = port if port != None else config.axon.port
        config.axon.ip = ip if ip != None else config.axon.ip
        config.axon.max_workers = max_workers if max_workers != None else config.axon.max_workers
        config.axon.maximum_concurrent_rpcs = maximum_concurrent_rpcs if maximum_concurrent_rpcs != None else config.axon.maximum_concurrent_rpcs
        config.axon.forward_timeout = forward_timeout if forward_timeout != None else config.axon.forward_timeout
        config.axon.backward_timeout = backward_timeout if backward_timeout != None else config.axon.backward_timeout
        config.axon.compression = compression if compression != None else config.axon.compression
        axon.check_config( config )

        # Determine the grpc compression algorithm
        if config.axon.compression == 'gzip':
            compress_alg = grpc.Compression.Gzip
        elif config.axon.compression == 'deflate':
            compress_alg = grpc.Compression.Deflate
        else:
            compress_alg = grpc.Compression.NoCompression

        if wallet == None:
            wallet = bittensor.wallet( config = config )
        if thread_pool == None:
            thread_pool = futures.ThreadPoolExecutor( max_workers = config.axon.max_workers )
        if server == None:
            server = grpc.server( thread_pool,
                                  interceptors=(AuthInterceptor(blacklist=blacklist),),
                                  maximum_concurrent_rpcs = config.axon.maximum_concurrent_rpcs,
                                  options = [('grpc.keepalive_time_ms', 100000),
                                             ('grpc.keepalive_timeout_ms', 500000)]
                                )

        synapses = {}
        synapses[bittensor.proto.Synapse.SynapseType.TEXT_LAST_HIDDEN_STATE] = synapse_last_hidden
        synapses[bittensor.proto.Synapse.SynapseType.TEXT_CAUSAL_LM] = synapse_causal_lm
        synapses[bittensor.proto.Synapse.SynapseType.TEXT_CAUSAL_LM_NEXT] = synapse_causal_lm_next
        synapses[bittensor.proto.Synapse.SynapseType.TEXT_SEQ_2_SEQ] = synapse_seq_2_seq
        
        synapse_check_function = synapse_checks if synapse_checks != None else axon.default_synapse_check

        if priority != None:
            priority_threadpool = bittensor.prioritythreadpool(config=config)
        else: 
            priority_threadpool = None

        axon_instance = axon_impl.Axon( 
            wallet = wallet, 
            server = server,
            ip = config.axon.ip,
            port = config.axon.port,
            forward = forward_text,
            backward = backward_text,
            synapses = synapses,
            synapse_checks = synapse_check_function,
            priority = priority,
            priority_threadpool = priority_threadpool,
            forward_timeout = config.axon.forward_timeout,
            backward_timeout = config.axon.backward_timeout,
        )
        bittensor.grpc.add_BittensorServicer_to_server( axon_instance, server )
        full_address = str( config.axon.ip ) + ":" + str( config.axon.port )
        server.add_insecure_port( full_address )
        return axon_instance 

    @classmethod   
    def config(cls) -> 'bittensor.Config':
        """ Get config from the argument parser
        Return: bittensor.config object
        """
        parser = argparse.ArgumentParser()
        axon.add_args( parser )
        return bittensor.config( parser )

    @classmethod   
    def help(cls):
        """ Print help to stdout
        """
        parser = argparse.ArgumentParser()
        cls.add_args( parser )
        print (cls.__new__.__doc__)
        parser.print_help()

    @classmethod
    def add_args( cls, parser: argparse.ArgumentParser, prefix: str = None  ):
        """ Accept specific arguments from parser
        """
        prefix_str = '' if prefix == None else prefix + '.'
        try:
            parser.add_argument('--' + prefix_str + 'axon.port', type=int, 
                    help='''The port this axon endpoint is served on. i.e. 8091''', default = bittensor.defaults.axon.port)
            parser.add_argument('--' + prefix_str + 'axon.ip', type=str, 
                help='''The local ip this axon binds to. ie. [::]''', default = bittensor.defaults.axon.ip)
            parser.add_argument('--' + prefix_str + 'axon.max_workers', type=int, 
                help='''The maximum number connection handler threads working simultaneously on this endpoint. 
                        The grpc server distributes new worker threads to service requests up to this number.''', default = bittensor.defaults.axon.max_workers)
            parser.add_argument('--' + prefix_str + 'axon.maximum_concurrent_rpcs', type=int, 
                help='''Maximum number of allowed active connections''',  default = bittensor.defaults.axon.maximum_concurrent_rpcs)
            parser.add_argument('--' + prefix_str + 'axon.backward_timeout', type=int,
                help='Number of seconds to wait for backward axon request', default=2*bittensor.__blocktime__)
            parser.add_argument('--' + prefix_str + 'axon.forward_timeout', type=int,
                help='Number of seconds to wait for forward axon request', default=5*bittensor.__blocktime__)
            parser.add_argument('--' + prefix_str + 'axon.priority.max_workers', type = int,
                help='''maximum number of threads in thread pool''', default = bittensor.defaults.axon.priority.max_workers)
            parser.add_argument('--' + prefix_str + 'axon.priority.maxsize', type=int, 
                help='''maximum size of tasks in priority queue''', default = bittensor.defaults.axon.priority.maxsize)
            parser.add_argument('--' + prefix_str + 'axon.compression', type=str, 
                help='''Which compression algorithm to use for compression (gzip, deflate, NoCompression) ''', default = bittensor.defaults.axon.compression)
        except argparse.ArgumentError:
            # re-parsing arguments.
            pass

        bittensor.wallet.add_args( parser, prefix = prefix )

    @classmethod   
    def add_defaults(cls, defaults):
        """ Adds parser defaults to object from enviroment variables.
        """
        defaults.axon = bittensor.config()
        defaults.axon.port = os.getenv('BT_AXON_PORT') if os.getenv('BT_AXON_PORT') != None else 8091
        defaults.axon.ip = os.getenv('BT_AXON_IP') if os.getenv('BT_AXON_IP') != None else '[::]'
        defaults.axon.max_workers = os.getenv('BT_AXON_MAX_WORERS') if os.getenv('BT_AXON_MAX_WORERS') != None else 10
        defaults.axon.maximum_concurrent_rpcs = os.getenv('BT_AXON_MAXIMUM_CONCURRENT_RPCS') if os.getenv('BT_AXON_MAXIMUM_CONCURRENT_RPCS') != None else 400
        
        defaults.axon.priority = bittensor.config()
        defaults.axon.priority.max_workers = os.getenv('BT_AXON_PRIORITY_MAX_WORKERS') if os.getenv('BT_AXON_PRIORITY_MAX_WORKERS') != None else 10
        defaults.axon.priority.maxsize = os.getenv('BT_AXON_PRIORITY_MAXSIZE') if os.getenv('BT_AXON_PRIORITY_MAXSIZE') != None else -1

        defaults.axon.compression = 'NoCompression'

    @classmethod   
    def check_config(cls, config: 'bittensor.Config' ):
        """ Check config for axon port and wallet
        """
        assert config.axon.port > 1024 and config.axon.port < 65535, 'port must be in range [1024, 65535]'
        bittensor.wallet.check_config( config )

    @classmethod   
    def default_synapse_check(cls, synapse, hotkey ):
        """ default synapse check function
        """
        if len(hotkey) == bittensor.__ss58_address_length__:
            return True
        
        return False

    @staticmethod
    def check_backward_callback( backward_callback:Callable, pubkey:str = '_' ):
        """ Check and test axon backward callback function
        """
        if not inspect.ismethod(backward_callback) and not inspect.isfunction(backward_callback):
            raise ValueError('The axon backward callback must be a function with signature Callable[inputs_x:torch.FloatTensor, grads_dy:torch.FloatTensor ) -> torch.FloatTensor:, got {}'.format(backward_callback))        
        if len( inspect.signature(backward_callback).parameters) != 3:
            raise ValueError('The axon backward callback must have signature Callable[ inputs_x:torch.FloatTensor, grads_dy:torch.FloatTensor, synapses ) -> torch.FloatTensor:, got {}'.format(inspect.signature(backward_callback)))
        if 'inputs_x' not in inspect.signature(backward_callback).parameters:
            raise ValueError('The axon backward callback must have signature Callable[inputs_x:torch.FloatTensor, grads_dy:torch.FloatTensor ) -> torch.FloatTensor:, got {}'.format(inspect.signature(backward_callback)))
        if 'grads_dy' not in inspect.signature(backward_callback).parameters:
            raise ValueError('The axon backward callback must have signature Callable[inputs_x:torch.FloatTensor, grads_dy:torch.FloatTensor ) -> torch.FloatTensor:, got {}'.format(inspect.signature(backward_callback)))
 

    @staticmethod
    def check_forward_callback( forward_callback:Callable, synapses:list = []):
        """ Check and test axon forward callback function
        """
        if not inspect.ismethod(forward_callback) and not inspect.isfunction(forward_callback):
            raise ValueError('The axon forward callback must be a function with signature Callable[inputs_x: torch.Tensor] -> torch.FloatTensor:, got {}'.format(forward_callback))   
        if len( inspect.signature(forward_callback).parameters) != 3:
            raise ValueError('The axon forward callback must have signature Callable[ inputs_x: torch.Tensor, synapses, hotkey] -> torch.FloatTensor:, got {}'.format(inspect.signature(forward_callback)))
        if 'inputs_x' not in inspect.signature(forward_callback).parameters:
            raise ValueError('The axon forward callback must have signature Callable[ inputs_x: torch.Tensor] -> torch.FloatTensor:, got {}'.format(inspect.signature(forward_callback)))
        
        sample_input = torch.randint(0,1,(3, 3))
        forward_callback([sample_input], synapses, hotkey='')

class AuthInterceptor(grpc.ServerInterceptor):
    """ Creates a new server interceptor that authenticates incoming messages from passed arguments.
    """
    def __init__(self, key:str = 'Bittensor',blacklist:List = []):
        r""" Creates a new server interceptor that authenticates incoming messages from passed arguments.
        Args:
            key (str, `optional`):
                 key for authentication header in the metadata (default= Bittensor)
            black_list (Fucntion, `optional`): 
                black list function that prevents certain pubkeys from sending messages
        """
        super().__init__()
        self._valid_metadata = ('rpc-auth-header', key)
        self.nounce_dic = {}
        self.message = 'Invalid key'
        self.blacklist = blacklist
        def deny(_, context):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, self.message)

        self._deny = grpc.unary_unary_rpc_method_handler(deny)

    def intercept_service(self, continuation, handler_call_details):
        r""" Authentication between bittensor nodes. Intercepts messages and checks them
        """
        meta = handler_call_details.invocation_metadata

        try: 
            #version checking
            self.version_checking(meta)

            #signature checking
            self.signature_checking(meta)

            #blacklist checking
            self.black_list_checking(meta)

            return continuation(handler_call_details)

        except Exception as e:
            self.message = str(e)
            return self._deny


    def vertification(self,meta):
        r"""vertification of signature in metadata. Uses the pubkey and nounce
        """
        variable_length_messages = meta[1].value.split('bitxx')
        nounce = int(variable_length_messages[0])
        pubkey = variable_length_messages[1]
        message = variable_length_messages[2]
        unique_receptor_uid = variable_length_messages[3]
        _keypair = Keypair(ss58_address=pubkey)

        # Unique key that specifies the endpoint.
        endpoint_key = str(pubkey) + str(unique_receptor_uid)
        
        #checking the time of creation, compared to previous messages
        if endpoint_key in self.nounce_dic.keys():
            prev_data_time = self.nounce_dic[ endpoint_key ]
            if nounce - prev_data_time > -10:
                self.nounce_dic[ endpoint_key ] = nounce

                #decrypting the message and verify that message is correct
                verification = _keypair.verify( str(nounce) + str(pubkey) + str(unique_receptor_uid), message)
            else:
                verification = False
        else:
            self.nounce_dic[ endpoint_key ] = nounce
            verification = _keypair.verify( str( nounce ) + str(pubkey) + str(unique_receptor_uid), message)

        return verification

    def signature_checking(self,meta):
        r""" Calls the vertification of the signature and raises an error if failed
        """
        if self.vertification(meta):
            pass
        else:
            raise Exception('Incorrect Signature')

    def version_checking(self,meta):
        r""" Checks the header and version in the metadata
        """
        if meta[0] == self._valid_metadata:
            pass
        else:
            raise Exception('Incorrect Metadata format')

    def black_list_checking(self,meta):
        r"""Tries to call to blacklist function in the miner and checks if it should blacklist the pubkey 
        """
        variable_length_messages = meta[1].value.split('bitxx')
        pubkey = variable_length_messages[1]
        
        if self.blacklist == None:
            pass
        elif self.blacklist(pubkey,int(meta[3].value)):
            raise Exception('Black listed')
        else:
            pass

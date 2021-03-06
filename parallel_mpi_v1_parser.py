from __future__ import division
import numpy as np
import itertools
import pandas as pd
import math
import operator
import time
from mpi4py import MPI
import argparse

import jodys_serial_v2 as serialv

def vals_of_attributes(D,n):
    output = []
    for i in xrange(n):
        output.append(list(np.unique(D[:,i])))
    return output

def alpha(df, mask):
    _df = df
    for combo in mask:
        _df = _df[_df[combo[0]] == combo[1]] 
    return len(_df)

def f(i,pi,attribute_values,df):

    len_pi = len(pi)

    phi_i_ = [attribute_values[item] for item in pi]
    if len(phi_i_) == 1:
        phi_i = [[item] for item in phi_i_[0]]
    else:
        phi_i = list(itertools.product(*phi_i_))

    # bug fix: phi_i might contain empty tuple (), which shouldn't be counted in q_I
    try:
        phi_i.remove(())
    except ValueError:
        pass

    q_i = len(phi_i)

    V_i = attribute_values[i]
    r_i = len(V_i)

    #product = 1
    product = 0
    #numerator = math.factorial(r_i - 1)
    numerator = np.sum([np.log(b) for b in range(1, r_i)])

    # special case: q_i = 0
    if q_i == 0:
        js = ['special']
    else:
        js = range(q_i) 

    for j  in js:

        # initializing mask to send to alpha
        if j == 'special':
            mask = []
        else:
            mask = zip(pi,phi_i[j])

        # initializing counts that will increase with alphas
        N_ij = 0
        #inner_product = 1
        inner_product = 0

        for k in xrange(r_i):
            # adjusting mask for each k
            mask_with_k = mask + [[i,V_i[k]]]
            alpha_ijk = alpha(df,mask_with_k)
            N_ij += alpha_ijk
            #inner_product = inner_product*math.factorial(alpha_ijk)
            inner_product = inner_product + np.sum([np.log(b) for b in range(1, alpha_ijk+1)])
        #denominator = math.factorial(N_ij + r_i - 1)
        denominator = np.sum([np.log(b) for b in range(1, N_ij+r_i)])
        #product = product*(numerator/denominator)*inner_product
        product = product + numerator - denominator + inner_product
    return product

def my_job(i,rank,size):
    flag = False
    if np.floor(i/size) % 2 == 0 and i%size == rank:
        flag = True
    if np.floor(i/size) % 2 == 1 and size - 1 - i%size  == rank:
        flag = True
    return flag

def k2_in_parallel(D,node_order,comm,rank,size,u=2):
    n = D.shape[1]
    assert len(node_order) == n, "Node order is not correct length.  It should have length %r" % n
    assert u < n, "Error:  u must be strictly less than n"
    m = D.shape[0]
    attribute_values = vals_of_attributes(D,n)

    df = pd.DataFrame(D)
    OKToProceed = False
    parents = {}

    for i in xrange(n):
        if my_job(i,rank,size) == True:
            OKToProceed = False
            pi = []
            pred = node_order[0:i]
            P_old = f(node_order[i],pi,attribute_values,df)
            if len(pred) > 0:
                OKToProceed = True
            while (OKToProceed == True and len(pi) < u):
                iters = [item for item in pred if item not in pi]
                if len(iters) > 0:
                    f_to_max = {};
                    for z_hat in iters:
                        f_to_max[z_hat] = f(node_order[i],pi+[z_hat],attribute_values,df)
                    z = max(f_to_max.iteritems(), key=operator.itemgetter(1))[0]
                    P_new = f_to_max[z]
                    if P_new > P_old:
                        P_old = P_new
                        pi = pi+[z]
                    else:
                        OKToProceed = False
                else:
                    OKToProceed = False
            parents[node_order[i]] = pi

    # sending parents back to node 0 for sorting and printing
    if rank == 0:
        for i in xrange(1,size):
            new_parents = comm.recv(source = i)
            parents.update(new_parents)
        return parents

    else:
        comm.send(parents,dest = 0)



if __name__ == "__main__":

    parser = argparse.ArgumentParser(description = '''K2 In Parallel:  Calculates the parent set for each node in your data file and returns a dictionary of the form
                                                                                    {feature: [parent set]}.''', usage='mpiexec -n <processes> python %(prog)s [options]' )
    parser.add_argument('-D', nargs='?', default = None, help='''Path to csc file containing a 0/1 array with m observations (rows) and n features (columns).  
                                                                                                A value of 1 represents the presence of that feature in that observation.''')
    parser.add_argument('--node_order', '-o', nargs='?',  type = list, default = None, help='''A list of integers containing the column order of features in your matrix.  
                                                                                                                                                If not provided, order the features in accordance with their order in the file.''')
    parser.add_argument('--random', '-r', action = "store_true", help='''Include this option to calculate parents for a random matrix.  If --random is included,
                                                                                                                 -D and --node_order should be left out, and -m and -n can be included.''')
    parser.add_argument('-n', nargs='?', type = int, default = '10', help='The number of features in a random matrix.  default is 10.  Only use with --random')
    parser.add_argument('-m', nargs='?', type = int, default = '100',  help='The number of observations in a random matrix.  default is 100. only use with --random')
    parser.add_argument('-u', nargs='?', type = int, default = 2, help='The maximum number of parents per feature.  Default is 2.  Must be less than number of features.')
    args = parser.parse_args()

    u = args.u

    if args.random:
        n = args.n
        m = args.m
        D = np.random.binomial(1,0.9,size=(m,n))
        node_order = list(range(n))

    else:
        if rank == 0:
            print "Reading in array D"
        D = np.loadtxt(open(args.D))
        if args.node_order != None:
            node_order = args.node_order
        else:
            if rank == 0:
                print "Determining node order"
            n = np.int32(D.shape[1])
            node_order = list(range(n))

    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    size = comm.Get_size()

    if rank == 0:
        print "Calculating Parent sets"
    comm.barrier()
    start = MPI.Wtime()
    parents = k2_in_parallel(D,node_order,comm,rank,size,u=u)
    comm.barrier()
    end = MPI.Wtime()
    if rank == 0:
        print "Parallel computing time", end-start
        print parents

#!/usr/bin/env python
from mininet.net import *
from mininet.topo import *
from mininet.node import OVSSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.cli import CLI
from mininet.node import Node, RemoteController
from scipy.stats import truncnorm, tstd
from numpy.random import randint, uniform
from subprocess import *
import sys
import signal
from time import sleep, time
from datetime import datetime


class MulticastGroupDefinition(object):
    def __init__(self, src_host, dst_hosts, group_ip, mcast_port, echo_port):
        self.src_host = src_host
        self.dst_hosts = dst_hosts
        self.group_ip = group_ip
        self.mcast_port = mcast_port
        self.echo_port = echo_port
        
        self.src_process = None
        self.dst_processes = []
    
    def launch_mcast_applications(self, net):
        # print 'Initializing multicast group ' + str(self.group_ip) + ':' + str(self.mcast_port) + ' Echo port: ' + str(self.echo_port)
        with open(os.devnull, "w") as fnull:
            # self.src_process = net.get(self.src_host).popen(['python', './multicast_sender.py', self.group_ip, str(self.mcast_port), str(self.echo_port)], stdout=fnull, stderr=fnull, close_fds=True)
            vlc_command = ['vlc-wrapper', 'test_media.mp4', '-I', 'dummy', '--sout', '"#rtp{access=udp, mux=ts, proto=udp, dst=' + self.group_ip + ', port=' + str(self.mcast_port) + '}"', '--sout-keep', '--loop']
            # print 'Running: ' + ' '.join(vlc_command)
            self.src_process = net.get(self.src_host).popen(' '.join(vlc_command), stdout=fnull, stderr=fnull, close_fds=True, shell=True, preexec_fn=os.setsid)
            
        for dst in self.dst_hosts:
            with open(os.devnull, "w") as fnull:
                # self.dst_processes.append(net.get(dst).popen(['python', './multicast_receiver.py', self.group_ip, str(self.mcast_port), str(self.echo_port)], stdout=fnull, stderr=fnull, close_fds=True))
                vlc_rcv_command = ['python', './multicast_receiver_VLC.py', self.group_ip, str(self.mcast_port), str(self.echo_port)]
                # print 'Running: ' + ' '.join(vlc_rcv_command)
                self.dst_processes.append(net.get(dst).popen(vlc_rcv_command, stdout=fnull, stderr=fnull, close_fds=True, shell=False))
        
        print('Initialized multicast group ' + str(self.group_ip) + ':' + str(self.mcast_port)
                + ' Echo port: ' + str(self.echo_port) + ' # Receivers: ' + str(len(self.dst_processes)))
    
    def terminate_mcast_applications(self):
        if self.src_process is not None:
            # print 'Killing process with PID: ' + str(self.src_process.pid)
            os.killpg(self.src_process.pid, signal.SIGTERM)
            
        for proc in self.dst_processes:
            # print 'Killing process with PID: ' + str(proc.pid)
            proc.send_signal(signal.SIGTERM)
        
        print 'Signaled termination of multicast group ' + str(self.group_ip) + ':' + str(self.mcast_port) + ' Echo port: ' + str(self.echo_port)

    def wait_for_application_termination(self):
        if self.src_process is not None:
            self.src_process.wait()
            self.src_process = None
        
        for proc in self.dst_processes:
            proc.wait()
        self.dst_processes = []

def generate_group_membership_probabilities(hosts, mean, std_dev, avg_group_size = 0):
    num_hosts = len(hosts)
    a , b = a, b = (0 - mean) / std_dev, (1 - mean) / std_dev
    midpoint_ab = (b + a) / 2
    scale = 1 / (b - a)
    location = 0.5 - (midpoint_ab * scale)
    rv = truncnorm(a, b, loc=location, scale=scale)
    rvs = rv.rvs(num_hosts)
    if avg_group_size > 0:
        rvs_sum = sum(rvs)
        rvs = [p / (rvs_sum/float(avg_group_size)) for p in rvs]
        rvs_sum = sum(rvs)
        rvs = [p / (rvs_sum/float(avg_group_size)) for p in rvs]
        
    prob_tuples = []
    for index, host in enumerate(hosts):
        prob_tuples.append((host, rvs[index]))
    
    return prob_tuples


def write_final_stats_log(final_log_path, flow_stats_file_path, event_log_file_path, membership_mean, membership_std_dev, membership_avg_bound, test_groups, group_launch_times, topography):
    def write_current_stats(log_file, link_bandwidth_usage_Mbps, switch_num_flows, cur_group_index, group):
        link_bandwidth_list = []
        total_num_flows = 0
        
        for switch_dpid in link_bandwidth_usage_Mbps:
            for port_no in link_bandwidth_usage_Mbps[switch_dpid]:
                link_bandwidth_list.append(link_bandwidth_usage_Mbps[switch_dpid][port_no])
        
        for switch_dpid in switch_num_flows:
            total_num_flows += switch_num_flows[switch_dpid]
        
        average_link_bandwidth_usage = sum(link_bandwidth_list) / float(len(link_bandwidth_list))
        traffic_concentration = 0
        if average_link_bandwidth_usage != 0:
            traffic_concentration = max(link_bandwidth_list) / average_link_bandwidth_usage
        link_util_std_dev = tstd(link_bandwidth_list)
        log_file.write('Group:' + str(cur_group_index))
        log_file.write(' NumReceivers:' + str(len(group.dst_hosts)))
        log_file.write(' TotalNumFlows:' + str(total_num_flows))
        log_file.write(' MaxLinkUsageMbps:' + str(max(link_bandwidth_list)))
        log_file.write(' AvgLinkUsageMbps:' + str(average_link_bandwidth_usage))
        log_file.write(' TrafficConcentration:' + str(traffic_concentration))
        log_file.write(' LinkUsageStdDev:' + str(link_util_std_dev))
        log_file.write('\n')
 
    switch_num_flows = {}   # Dictionary of number of currently installed flows, keyed by switch_dpid
    link_bandwidth_usage_Mbps = {} # Dictionary of dictionaries: link_bandwidth_usage_Mbps[switch_dpid][port_no]
    cur_group_index = 0
    cur_time = 0
    cur_switch_dpid = None
    
    final_log_file = open(final_log_path, 'w')
    # Write out scenario params
    num_receivers_list = []
    for group in test_groups:
        num_receivers_list.append(len(group.dst_hosts))
    avg_num_receivers = sum(num_receivers_list) / float(len(num_receivers_list))
    
    final_log_file.write('GroupFlow Performance Simulation: ' + str(datetime.now()) + '\n')
    final_log_file.write('FlowStatsLogFile:' + str(flow_stats_file_path) + '\n')
    final_log_file.write('EventTraceLogFile:' + str(event_log_file_path) + '\n')
    final_log_file.write('Membership Mean:' + str(membership_mean) + ' StdDev:' + str(membership_std_dev) + ' AvgBound:' + str(membership_avg_bound) + ' NumGroups:' + str(len(test_groups)) + ' AvgNumReceivers:' + str(avg_num_receivers) + '\n')
    final_log_file.write('Topology:' + str(topography) + ' NumSwitches:' + str(len(topography.switches())) + ' NumLinks:' + str(len(topography.links())) + ' NumHosts:' + str(len(topography.hosts())) + '\n')
    
    flow_log_file = open(flow_stats_file_path, 'r')
    for line in flow_log_file:
        # This line specifies that start of stats for a new switch and time instant
        if 'FlowStats' in line:
            line_split = line.split()
            switch_dpid = line_split[1][7:]
            num_flows = int(line_split[2][9:])
            cur_time = float(line_split[4][16:])
            
            cur_switch_dpid = switch_dpid
            
            # print 'Got stats for switch: ' + str(switch_dpid)
            # print 'Cur Time: ' + str(cur_time) + '    Next Group Launch: ' + str(group_launch_times[cur_group_index])
            
            # First, check to see if a new group has been initialized before this time, and log the current flow stats if so
            if cur_group_index < len(group_launch_times) and cur_time > group_launch_times[cur_group_index]:
                cur_group_index += 1
                if(cur_group_index > 1):
                    write_current_stats(final_log_file, link_bandwidth_usage_Mbps, switch_num_flows, cur_group_index - 2, test_groups[cur_group_index - 2])
            
            switch_num_flows[cur_switch_dpid] = num_flows
            
        # This line specifies port specific stats for the last referenced switch
        if 'Port' in line:
            line_split = line.split()
            port_no = int(line_split[0][5:])
            bandwidth_usage = float(line_split[3][13:])
            if(port_no == 65533):
                # Ignore connections to the controller for these calculations
                continue
                
            if cur_switch_dpid not in link_bandwidth_usage_Mbps:
                link_bandwidth_usage_Mbps[cur_switch_dpid] = {}
            link_bandwidth_usage_Mbps[cur_switch_dpid][port_no] = bandwidth_usage
    
    # Print the stats for the final multicast group
    write_current_stats(final_log_file, link_bandwidth_usage_Mbps, switch_num_flows, cur_group_index - 1, test_groups[cur_group_index - 1])
    
    flow_log_file.close()
    final_log_file.close()


class BriteTopo(Topo):
    def __init__(self, brite_filepath):
        # Initialize topology
        Topo.__init__( self )
        
        self.hostnames = []
        self.routers = []
        self.file_path = brite_filepath
        
        print 'Parsing BRITE topology at filepath: ' + str(brite_filepath)
        file = open(brite_filepath, 'r')
        line = file.readline()
        print 'BRITE ' + line
        
        # Skip ahead until the nodes section is reached
        in_node_section = False
        while not in_node_section:
            line = file.readline()
            if 'Nodes:' in line:
                in_node_section = True
                break
        
        # In the nodes section now, generate a switch and host for each node
        while in_node_section:
            line = file.readline().strip()
            if not line:
                in_node_section = False
                print 'Finished parsing nodes'
                break
            
            line_split = line.split('\t')
            node_id = int(line_split[0])
            print 'Generating switch and host for ID: ' + str(node_id)
            switch = self.addSwitch('s' + str(node_id))
            host = self.addHost('h' + str(node_id))
            self.addLink(switch, host, bw=30, use_htb=True)	# TODO: Better define link parameters for hosts
            self.routers.append(switch)
            self.hostnames.append('h' + str(node_id))
            
        # Skip ahead to the edges section
        in_edge_section = False
        while not in_edge_section:
            line = file.readline()
            if 'Edges:' in line:
                in_edge_section = True
                break
        
        # In the edges section now, add all required links
        while in_edge_section:
            line = file.readline().strip()
            if not line:    # Empty string
                in_edge_section = False
                print 'Finished parsing edges'
                break
                
            line_split = line.split('\t')
            switch_id_1 = int(line_split[1])
            switch_id_2 = int(line_split[2])
            delay_ms = str(float(line_split[4])) + 'ms'
            bandwidth_Mbps = float(line_split[5])
            print 'Adding link between switch ' + str(switch_id_1) + ' and ' + str(switch_id_2) + '\n\tRate: ' \
                + str(bandwidth_Mbps) + ' Mbps\tDelay: ' + delay_ms
            # params = {'bw':bandwidth_Mbps, 'delay':delay_ms}]
            # TODO: Figure out why setting the delay won't work
            self.addLink(self.routers[switch_id_1], self.routers[switch_id_2], bw=bandwidth_Mbps, delay=delay_ms, max_queue_size=1000, use_htb=True)
        
        file.close()
    
    def get_host_list(self):
        return self.hostnames
    
    def mcastConfig(self, net):
        for hostname in self.hostnames:
            net.get(hostname).cmd('route add -net 224.0.0.0/4 ' + hostname + '-eth0')
    
    def __str__(self):
        return self.file_path

            
class MulticastTestTopo( Topo ):
    "Simple multicast testing example"
    
    def __init__( self ):
        "Create custom topo."
        
        # Initialize topology
        Topo.__init__( self )
        
        # Add hosts and switches
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')
        h3 = self.addHost('h3')
        h4 = self.addHost('h4')
        h5 = self.addHost('h5')
        h6 = self.addHost('h6')
        h7 = self.addHost('h7')
        h8 = self.addHost('h8')
        h9 = self.addHost('h9')
        h10 = self.addHost('h10')
        h11 = self.addHost('h11')
        
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        s3 = self.addSwitch('s3')
        s4 = self.addSwitch('s4')
        s5 = self.addSwitch('s5')
        s6 = self.addSwitch('s6')
        s7 = self.addSwitch('s7')
        
        # Add links
        self.addLink(s1, s2, bw = 10, use_htb = True)
        self.addLink(s1, s3, bw = 10, use_htb = True)
        self.addLink(s2, s4, bw = 10, use_htb = True)
        self.addLink(s4, s5, bw = 10, use_htb = True)
        self.addLink(s2, s5, bw = 10, use_htb = True)
        self.addLink(s2, s6, bw = 10, use_htb = True)
        self.addLink(s6, s3, bw = 10, use_htb = True)
        self.addLink(s3, s7, bw = 10, use_htb = True)
        self.addLink(s7, s5, bw = 10, use_htb = True)
        
        self.addLink(s2, h1, bw = 10, use_htb = True)
        self.addLink(s3, h2, bw = 10, use_htb = True)
        self.addLink(s3, h3, bw = 10, use_htb = True)
        self.addLink(s5, h4, bw = 10, use_htb = True)
        self.addLink(s5, h5, bw = 10, use_htb = True)
        self.addLink(s5, h6, bw = 10, use_htb = True)
        self.addLink(s2, h7, bw = 10, use_htb = True)
        self.addLink(s6, h8, bw = 10, use_htb = True)
        self.addLink(s7, h9, bw = 10, use_htb = True)
        self.addLink(s4, h10, bw = 10, use_htb = True)
        self.addLink(s1, h11, bw = 10, use_htb = True)

    def mcastConfig(self, net):
        # Configure hosts for multicast support
        net.get('h1').cmd('route add -net 224.0.0.0/4 h1-eth0')
        net.get('h2').cmd('route add -net 224.0.0.0/4 h2-eth0')
        net.get('h3').cmd('route add -net 224.0.0.0/4 h3-eth0')
        net.get('h4').cmd('route add -net 224.0.0.0/4 h4-eth0')
        net.get('h5').cmd('route add -net 224.0.0.0/4 h5-eth0')
        net.get('h6').cmd('route add -net 224.0.0.0/4 h6-eth0')
        net.get('h7').cmd('route add -net 224.0.0.0/4 h7-eth0')
        net.get('h8').cmd('route add -net 224.0.0.0/4 h8-eth0')
        net.get('h9').cmd('route add -net 224.0.0.0/4 h9-eth0')
        net.get('h10').cmd('route add -net 224.0.0.0/4 h10-eth0')
        net.get('h11').cmd('route add -net 224.0.0.0/4 h11-eth0')
    
    def get_host_list(self):
        return ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'h7', 'h8', 'h9', 'h10', 'h11']

        
def mcastTest(topo, interactive = False, hosts = [], log_file_name = 'test_log.log', util_link_weight = 10, link_weight_type = 'linear'):
    membership_mean = 0.1
    membership_std_dev = 0.25
    membership_avg_bound = 5
    test_groups = []
    test_group_launch_times = []
    
    # Launch the external controller
    pox_arguments = ['pox.py', 'log', '--file=pox.log,w', 'openflow.discovery',
            'openflow.flow_tracker', '--query_interval=1', '--link_max_bw=30', '--link_cong_threshold=28.5', '--avg_smooth_factor=0.65', '--log_peak_usage=True',
            'misc.benchmark_terminator', 'misc.groupflow_event_tracer', 'openflow.igmp_manager', 
            'openflow.groupflow', '--util_link_weight=' + str(util_link_weight), '--link_weight_type=' + link_weight_type,
            'log.level', '--WARNING', '--openflow.flow_tracker=INFO']
    print 'Launching external controller: ' + str(pox_arguments[0])
    print 'Launch arguments:'
    print ' '.join(pox_arguments)
    
    with open(os.devnull, "w") as fnull:
        pox_process = Popen(pox_arguments, stdout=fnull, stderr=fnull, shell=False, close_fds=True)
        # Allow time for the log file to be generated
        sleep(1)
    
    # Determine the flow tracker log file
    pox_log_file = open('./pox.log', 'r')
    flow_log_path = None
    event_log_path = None
    got_flow_log_path = False
    got_event_log_path = False
    while (not got_flow_log_path) or (not got_event_log_path):
        pox_log = pox_log_file.readline()

        if 'Writing flow tracker info to file:' in pox_log:
            pox_log_split = pox_log.split()
            flow_log_path = pox_log_split[-1]
            got_flow_log_path = True
        
        if 'Writing event trace info to file:' in pox_log:
            pox_log_split = pox_log.split()
            event_log_path = pox_log_split[-1]
            got_event_log_path = True
            
            
    print 'Got flow tracker log file: ' + str(flow_log_path)
    print 'Got event trace log file: ' + str(event_log_path)
    print 'Controller initialized'
    pox_log_offset = pox_log_file.tell()
    pox_log_file.close()
    
    # External controller
    net = Mininet(topo, controller=RemoteController, switch=OVSSwitch, link=TCLink, build=False, autoSetMacs=True)
    # pox = RemoteController('pox', '127.0.0.1', 6633)
    net.addController('pox', RemoteController, ip = '127.0.0.1', port = 6633)
    net.start()
    topo.mcastConfig(net)
    sleep_time = 8 + (float(len(hosts))/8)
    print 'Waiting ' + str(sleep_time) + ' seconds to allow for controller topology discovery'
    sleep(sleep_time)   # Allow time for the controller to detect the topology
    
    if interactive:
        CLI(net)
    else:
        mcast_group_last_octet = 1
        mcast_port = 5010
        host_join_probabilities = generate_group_membership_probabilities(hosts, membership_mean, membership_std_dev, membership_avg_bound)
        print 'Host join probabilities: ' + ', '.join(str(p) for p in host_join_probabilities)
        host_join_sum = sum(p[1] for p in host_join_probabilities)
        print 'Measured mean join probability: ' + str(host_join_sum / len(host_join_probabilities))
        print 'Predicted average group size: ' + str(host_join_sum)
        i = 1
        while True:
            print 'Generating multicast group #' + str(i)
            # Choose a sending host using a uniform random distribution
            sender_index = randint(0,len(hosts))
            sender_host = hosts[sender_index]
            
            # Choose a random number of receivers by comparing a uniform random variable
            # against the previously generated group membership probabilities
            receivers = []
            for host_prob in host_join_probabilities:
                p = uniform(0, 1)
                if p <= host_prob[1]:
                    receivers.append(host_prob[0])
            
            # Initialize the group
            # Note - This method of group IP generation will need to be modified slightly to support more than
            # 255 groups
            mcast_ip = '224.1.1.{last_octet}'.format(last_octet = str(mcast_group_last_octet))
            test_groups.append(MulticastGroupDefinition(sender_host, receivers, mcast_ip, mcast_port, mcast_port + 1))
            launch_time = time()
            test_group_launch_times.append(launch_time)
            print 'Launching multicast group #' + str(i) + ' at time: ' + str(launch_time)
            test_groups[-1].launch_mcast_applications(net)
            mcast_group_last_octet = mcast_group_last_octet + 1
            mcast_port = mcast_port + 2
            i += 1
            sleep(8)
            
            # Read from the log file to determine if a link has become overloaded, and cease generating new groups if so
            print 'Check for congested link...'
            congested_link = False
            pox_log_file = open('./pox.log', 'r')
            pox_log_file.seek(pox_log_offset)
            for line in pox_log_file:
                if 'Network peak link throughout (MBps):' in line:
                    line_split = line.split(' ')
                    print 'Peak Usage (Mbps): ' + line_split[-1],
                if 'Network avg link throughout (MBps):' in line:
                    line_split = line.split(' ')
                    print 'Mean Usage (Mbps): ' + line_split[-1],
                if 'Congested link detected!' in line:
                    congested_link = True
                    break
            pox_log_offset = pox_log_file.tell()
            pox_log_file.close()
            if congested_link:
                print 'Detected congested link, terminating simulation.'
                break
            else:
                print 'No congestion detected.'

    
    print 'Terminating network applications'
    for group in test_groups:
        group.terminate_mcast_applications()
    print 'Terminating controller'
    pox_process.send_signal(signal.SIGINT)
    print 'Waiting for network application termination...'
    for group in test_groups:
        group.wait_for_application_termination()
    print 'Network applications terminated'
    print 'Waiting for controller termination...'
    pox_process.wait()
    print 'Controller terminated'
    pox_process = None
    net.stop()
    
    # Make extra sure the network terminated cleanly
    call(['mn', '-c'])
    
    # Make extra sure all VLC instances were killed
    ps_out = os.popen('ps -e')
    for line in ps_out:
        if 'vlc' in line:
            line_split = line.strip().split(' ')
            proc_id = int(line_split[0])
            # print 'Sending SIGTERM to leftover VLC process: ' + line,
            os.kill(proc_id, signal.SIGTERM)
    
    if not interactive:
        write_final_stats_log(log_file_name, flow_log_path, event_log_path, membership_mean, membership_std_dev, membership_avg_bound, test_groups, test_group_launch_times, topo)

topos = { 'mcast_test': ( lambda: MulticastTestTopo() ) }

if __name__ == '__main__':
    setLogLevel( 'info' )
    if len(sys.argv) >= 6:
        # Automated simulations - Differing link usage weights in Groupflow Module
        log_prefix = sys.argv[3]
        num_iterations = int(sys.argv[2])
        first_index = int(sys.argv[4])
        util_params = []
        for param_index in range(5, len(sys.argv)):
            param_split = sys.argv[param_index].split(',')
            util_params.append((param_split[0], int(param_split[1])))
        topo = BriteTopo(sys.argv[1])
        hosts = topo.get_host_list()
        start_time = time()
        print 'Simulations started at: ' + str(datetime.now())
        for i in range(0,num_iterations):
            for util_param in util_params:
                sim_start_time = time()
                mcastTest(topo, False, hosts, log_prefix + '_' + ''.join([util_param[0], str(util_param[1])]) + '_' + str(i + first_index) + '.log', util_param[1], util_param[0])
                sim_end_time = time()
                print 'Simulation ' + str(i+1) + '_u' + ''.join([util_param[0], str(util_param[1])]) + ' completed at: ' + str(datetime.now()) + ' (runtime: ' + str(sim_end_time - sim_start_time) + ' seconds)'
        end_time = time()
        print ' '
        print 'Simulations completed at: ' + str(datetime.now())
        print 'Total runtime: ' + str(end_time - start_time) + ' seconds'
        print 'Average runtime per sim: ' + str((end_time - start_time) / (num_iterations * len(util_params))) + ' seconds'
        
    elif len(sys.argv) >= 4:
        # Automated simulations - Same module parameters for all runs
        log_prefix = sys.argv[3]
        num_iterations = int(sys.argv[2])
        topo = BriteTopo(sys.argv[1])
        hosts = topo.get_host_list()
        start_time = time()
        print 'Simulations started at: ' + str(datetime.now())
        for i in range(0,num_iterations):
            sim_start_time = time()
            mcastTest(topo, False, hosts, log_prefix + str(i) + '.log')
            sim_end_time = time()
            print 'Simulation ' + str(i+1) + ' completed at: ' + str(datetime.now()) + ' (runtime: ' + str(sim_start_time - sim_end_time) + ' seconds)'
        end_time = time()
        print ' '
        print 'Simulations completed at: ' + str(datetime.now())
        print 'Total runtime: ' + str(end_time - start_time) + ' seconds'
        print 'Average runtime per sim: ' + str((end_time - start_time) / num_iterations) + ' seconds'
        
    elif len(sys.argv) >= 2:
        # Interactive mode - configures POX and multicast routes, but no automatic traffic generation
        print 'Launching BRITE defined multicast test topology'
        topo = BriteTopo(sys.argv[1])
        hosts = topo.get_host_list()
        mcastTest(topo, True, hosts)
        
    else:
        # Interactive mode with barebones topology
        print 'Launching default multicast test topology'
        topo = MulticastTestTopo()
        hosts = topo.get_host_list()
        mcastTest(topo, hosts)

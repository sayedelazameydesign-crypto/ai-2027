const EventEmitter = require('events');
const winston = require('winston');
const databaseService = require('./database');
const redisService = require('./redis');
const Bull = require('bull');

class AgentManager extends EventEmitter {
  constructor(io) {
    super();
    this.io = io;
    this.agents = new Map();
    this.agentProcesses = new Map();
    this.taskQueues = new Map();
    this.healthCheckInterval = null;
    this.isInitialized = false;
    
    // Performance tracking
    this.performanceMetrics = {
      totalTasks: 0,
      completedTasks: 0,
      failedTasks: 0,
      averageResponseTime: 0,
      uptime: Date.now()
    };
  }

  async initialize() {
    try {
      winston.info('🚀 Initializing Agent Manager...');
      
      // Load existing agents from database
      await this.loadAgentsFromDatabase();
      
      // Initialize task queues for each agent type
      await this.initializeTaskQueues();
      
      // Start health monitoring
      this.startHealthMonitoring();
      
      this.isInitialized = true;
      this.emit('initialized');
      
      winston.info('✅ Agent Manager initialized successfully');
    } catch (error) {
      winston.error('❌ Agent Manager initialization failed:', error);
      throw error;
    }
  }

  async registerAgents(agents) {
    for (const agent of agents) {
      await this.registerAgent(agent);
    }
    winston.info(`🔄 Registered ${agents.length} agents`);
  }

  async registerAgent(agent) {
    try {
      const agentId = agent.getId();
      
      // Add agent to our registry
      this.agents.set(agentId, agent);
      
      // Set up agent event listeners
      this.setupAgentEventListeners(agent);
      
      // Initialize agent
      await agent.initialize();
      
      // Update database
      await this.updateAgentInDatabase(agent);
      
      // Set initial status
      await this.setAgentStatus(agentId, 'online', {
        version: agent.getVersion ? agent.getVersion() : '1.0.0',
        capabilities: agent.getCapabilities ? agent.getCapabilities() : []
      });
      
      // Create task queue for this agent if needed
      if (agent.needsTaskQueue && agent.needsTaskQueue()) {
        await this.createTaskQueue(agentId, agent.getTaskQueueConfig());
      }
      
      winston.info(`🤖 Agent registered: ${agent.getName()} (${agentId})`);
      
      // Notify connected clients
      this.io.emit('agent:registered', {
        agentId,
        name: agent.getName(),
        type: agent.getType(),
        status: 'online'
      });
      
    } catch (error) {
      winston.error(`Failed to register agent ${agent.getName()}:`, error);
      throw error;
    }
  }

  setupAgentEventListeners(agent) {
    const agentId = agent.getId();
    
    // Task completion events
    agent.on('task:completed', async (taskId, result) => {
      await this.handleTaskCompleted(agentId, taskId, result);
    });
    
    // Task failure events
    agent.on('task:failed', async (taskId, error) => {
      await this.handleTaskFailed(agentId, taskId, error);
    });
    
    // Status changes
    agent.on('status:changed', async (newStatus, metadata) => {
      await this.setAgentStatus(agentId, newStatus, metadata);
    });
    
    // Log events
    agent.on('log', async (level, message, metadata) => {
      await this.createAgentLog(agentId, level, message, metadata);
    });
    
    // Performance metrics
    agent.on('metric', async (metricName, value, metadata) => {
      await this.recordPerformanceMetric(agentId, metricName, value, metadata);
    });
  }

  async loadAgentsFromDatabase() {
    try {
      const agents = await databaseService.getAgents();
      winston.info(`📊 Loaded ${agents.length} agents from database`);
      
      // Update status for existing agents
      for (const dbAgent of agents) {
        if (this.agents.has(dbAgent.id)) {
          const agent = this.agents.get(dbAgent.id);
          await agent.setStatus(dbAgent.status);
          await agent.setConfiguration(dbAgent.config);
        }
      }
    } catch (error) {
      winston.error('Failed to load agents from database:', error);
    }
  }

  async initializeTaskQueues() {
    for (const [agentId, agent] of this.agents) {
      if (agent.needsTaskQueue && agent.needsTaskQueue()) {
        await this.createTaskQueue(agentId, agent.getTaskQueueConfig());
      }
    }
  }

  async createTaskQueue(agentId, config = {}) {
    const queueName = `agent-tasks:${agentId}`;
    
    const queue = new Bull(queueName, {
      redis: {
        host: process.env.REDIS_HOST || 'localhost',
        port: process.env.REDIS_PORT || 6379,
        password: process.env.REDIS_PASSWORD,
      },
      defaultJobOptions: {
        removeOnComplete: 100,
        removeOnFail: 50,
        attempts: 3,
        backoff: {
          type: 'exponential',
          delay: 2000,
        },
      },
      ...config
    });
    
    // Process jobs
    queue.process(10, async (job) => {
      await this.processJob(agentId, job);
    });
    
    // Queue events
    queue.on('completed', (job, result) => {
      winston.info(`Job completed for agent ${agentId}:`, job.id);
    });
    
    queue.on('failed', (job, err) => {
      winston.error(`Job failed for agent ${agentId}:`, job.id, err);
    });
    
    this.taskQueues.set(agentId, queue);
    winston.info(`📋 Task queue created for agent ${agentId}`);
  }

  async processJob(agentId, job) {
    try {
      const agent = this.agents.get(agentId);
      if (!agent) {
        throw new Error(`Agent ${agentId} not found`);
      }
      
      winston.info(`🎯 Processing job ${job.id} for agent ${agentId}`);
      
      // Update task status
      await this.updateTaskStatus(job.data.taskId, 'processing');
      
      // Execute task
      const result = await agent.processTask(job.data);
      
      // Update task status
      await this.updateTaskStatus(job.data.taskId, 'completed', { result });
      
      return result;
    } catch (error) {
      winston.error(`Job processing failed for agent ${agentId}:`, error);
      
      // Update task status
      await this.updateTaskStatus(job.data.taskId, 'failed', { error: error.message });
      
      throw error;
    }
  }

  async assignTask(agentId, taskData, options = {}) {
    try {
      // Validate agent exists and is available
      const agent = this.agents.get(agentId);
      if (!agent) {
        throw new Error(`Agent ${agentId} not found`);
      }
      
      // Check if agent can accept new tasks
      if (!agent.canAcceptTasks || !await agent.canAcceptTasks()) {
        throw new Error(`Agent ${agentId} is not available for new tasks`);
      }
      
      // Create task in database
      const task = await databaseService.createTask({
        agent_id: agentId,
        assigned_by: options.assignedBy,
        task_type: taskData.type,
        title: taskData.title,
        description: taskData.description,
        payload: taskData,
        priority: taskData.priority || 1
      });
      
      // Add to task queue if available
      const queue = this.taskQueues.get(agentId);
      if (queue) {
        await queue.add('process-task', {
          taskId: task.id,
          ...taskData
        }, {
          priority: task.priority,
          delay: options.delay || 0,
          removeOnComplete: 100,
          removeOnFail: 50
        });
      } else {
        // Direct task execution for agents without queues
        await this.executeDirectTask(agentId, task);
      }
      
      // Notify agent
      if (agent.onTaskAssigned) {
        await agent.onTaskAssigned(task);
      }
      
      // Notify connected clients
      this.io.emit('task:assigned', {
        taskId: task.id,
        agentId,
        agentName: agent.getName(),
        task: task
      });
      
      winston.info(`📝 Task assigned to agent ${agentId}: ${task.id}`);
      
      return task;
    } catch (error) {
      winston.error('Task assignment failed:', error);
      throw error;
    }
  }

  async executeDirectTask(agentId, task) {
    try {
      const agent = this.agents.get(agentId);
      if (!agent) {
        throw new Error(`Agent ${agentId} not found`);
      }
      
      // Update task status
      await this.updateTaskStatus(task.id, 'processing');
      
      // Execute task
      const result = await agent.processTask(task.payload);
      
      // Update task status
      await this.updateTaskStatus(task.id, 'completed', { result });
      
      winston.info(`✅ Direct task completed: ${task.id}`);
    } catch (error) {
      winston.error(`Direct task failed: ${task.id}`, error);
      await this.updateTaskStatus(task.id, 'failed', { error: error.message });
    }
  }

  async handleTaskCompleted(agentId, taskId, result) {
    try {
      await this.updateTaskStatus(taskId, 'completed', { result });
      
      // Update performance metrics
      this.performanceMetrics.completedTasks++;
      this.performanceMetrics.totalTasks++;
      
      // Notify connected clients
      this.io.emit('task:completed', { taskId, agentId, result });
      
      winston.info(`✅ Task completed: ${taskId} by agent ${agentId}`);
    } catch (error) {
      winston.error('Failed to handle task completion:', error);
    }
  }

  async handleTaskFailed(agentId, taskId, error) {
    try {
      await this.updateTaskStatus(taskId, 'failed', { error: error.message });
      
      // Update performance metrics
      this.performanceMetrics.failedTasks++;
      this.performanceMetrics.totalTasks++;
      
      // Notify connected clients
      this.io.emit('task:failed', { taskId, agentId, error: error.message });
      
      winston.error(`❌ Task failed: ${taskId} by agent ${agentId}:`, error);
    } catch (err) {
      winston.error('Failed to handle task failure:', err);
    }
  }

  async setAgentStatus(agentId, status, metadata = {}) {
    try {
      const agent = this.agents.get(agentId);
      if (!agent) return;
      
      // Update agent status
      if (agent.setStatus) {
        await agent.setStatus(status);
      }
      
      // Update in database
      await databaseService.updateAgentStatus(agentId, status, metadata);
      
      // Cache in Redis
      await redisService.setAgentStatus(agentId, status, {
        ...metadata,
        updatedAt: new Date().toISOString()
      });
      
      // Notify connected clients
      this.io.emit('agent:status-changed', {
        agentId,
        status,
        metadata
      });
      
      winston.info(`📊 Agent ${agentId} status changed to: ${status}`);
    } catch (error) {
      winston.error(`Failed to update agent status ${agentId}:`, error);
    }
  }

  async updateAgentInDatabase(agent) {
    try {
      const agentId = agent.getId();
      await databaseService.query(`
        INSERT INTO agents (id, name, agent_type, description, status, config)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (id) 
        DO UPDATE SET 
          name = $2, 
          agent_type = $3, 
          description = $4, 
          config = $6, 
          updated_at = NOW()
      `, [
        agentId,
        agent.getName(),
        agent.getType(),
        agent.getDescription(),
        'online',
        JSON.stringify(agent.getConfiguration ? agent.getConfiguration() : {})
      ]);
    } catch (error) {
      winston.error('Failed to update agent in database:', error);
    }
  }

  async createAgentLog(agentId, level, message, metadata = {}) {
    try {
      await databaseService.createLog(agentId, level, message, metadata);
      
      // Also publish to Redis for real-time monitoring
      await redisService.publish('agent-logs', {
        agentId,
        level,
        message,
        metadata,
        timestamp: new Date().toISOString()
      });
      
      // Notify connected clients
      this.io.emit('agent:log', { agentId, level, message, metadata });
    } catch (error) {
      winston.error('Failed to create agent log:', error);
    }
  }

  async recordPerformanceMetric(agentId, metricName, value, metadata = {}) {
    try {
      await databaseService.createPerformanceMetric(
        agentId,
        metricName,
        value,
        metadata.type || 'gauge',
        metadata
      );
      
      // Cache in Redis for real-time access
      await redisService.cacheMetrics(agentId, {
        [metricName]: { value, timestamp: new Date().toISOString(), ...metadata }
      });
      
      // Notify connected clients
      this.io.emit('agent:metric', { agentId, metricName, value, metadata });
    } catch (error) {
      winston.error('Failed to record performance metric:', error);
    }
  }

  async updateTaskStatus(taskId, status, result = {}) {
    try {
      const updateData = { status };
      if (result.result) updateData.result = result.result;
      if (result.error) updateData.error_message = result.error;
      
      if (status === 'processing') {
        updateData.started_at = new Date();
      } else if (status === 'completed' || status === 'failed') {
        updateData.completed_at = new Date();
      }
      
      await databaseService.updateTask(taskId, updateData);
    } catch (error) {
      winston.error('Failed to update task status:', error);
    }
  }

  // Public API methods
  async getAgent(agentId) {
    return this.agents.get(agentId);
  }

  async getAllAgents() {
    const agents = [];
    for (const [agentId, agent] of this.agents) {
      agents.push({
        id: agentId,
        name: agent.getName(),
        type: agent.getType(),
        status: agent.getStatus ? await agent.getStatus() : 'unknown',
        description: agent.getDescription()
      });
    }
    return agents;
  }

  async getAgentStatus(agentId) {
    return await redisService.getAgentStatus(agentId);
  }

  async getTasks(agentId = null, status = null) {
    return await databaseService.getTasks(agentId, status);
  }

  async getPerformanceMetrics(agentId) {
    return await databaseService.getPerformanceMetrics(agentId);
  }

  async processTaskQueue() {
    for (const [agentId, queue] of this.taskQueues) {
      try {
        const waiting = await queue.getWaiting();
        const active = await queue.getActive();
        
        if (waiting.length === 0 && active.length === 0) {
          // Mark agent as idle
          await this.setAgentStatus(agentId, 'online', { state: 'idle' });
        }
      } catch (error) {
        winston.error(`Failed to process queue for agent ${agentId}:`, error);
      }
    }
  }

  async checkAgentHealth() {
    for (const [agentId, agent] of this.agents) {
      try {
        if (agent.healthCheck) {
          const health = await agent.healthCheck();
          
          if (!health.healthy) {
            await this.setAgentStatus(agentId, 'error', {
              health,
              lastCheck: new Date().toISOString()
            });
            
            // Attempt to restart failed agents
            if (health.restartable) {
              winston.info(`🔄 Attempting to restart failed agent: ${agentId}`);
              await this.restartAgent(agentId);
            }
          }
        }
      } catch (error) {
        winston.error(`Health check failed for agent ${agentId}:`, error);
        await this.setAgentStatus(agentId, 'error', {
          error: error.message,
          lastCheck: new Date().toISOString()
        });
      }
    }
  }

  async restartAgent(agentId) {
    try {
      const agent = this.agents.get(agentId);
      if (!agent) return;
      
      winston.info(`🔄 Restarting agent: ${agentId}`);
      
      // Set status to restarting
      await this.setAgentStatus(agentId, 'restarting');
      
      // Stop current agent
      if (agent.stop) {
        await agent.stop();
      }
      
      // Reinitialize
      await agent.initialize();
      
      // Set back to online
      await this.setAgentStatus(agentId, 'online');
      
      winston.info(`✅ Agent restarted successfully: ${agentId}`);
      
    } catch (error) {
      winston.error(`Failed to restart agent ${agentId}:`, error);
      await this.setAgentStatus(agentId, 'error', {
        restartFailed: true,
        error: error.message
      });
    }
  }

  async runSystemMonitoring() {
    try {
      const systemHealth = await databaseService.getSystemHealth();
      const redisInfo = await redisService.getRedisInfo();
      
      // Calculate overall system health
      const totalAgents = this.agents.size;
      const healthyAgents = Array.from(this.agents.values()).filter(agent => 
        agent.getStatus ? agent.getStatus() === 'online' : false
      ).length;
      
      const healthScore = (healthyAgents / totalAgents) * 100;
      
      await this.recordPerformanceMetric('system', 'health_score', healthScore, {
        type: 'gauge',
        description: 'Overall system health score'
      });
      
      await this.recordPerformanceMetric('system', 'healthy_agents', healthyAgents, {
        type: 'gauge',
        description: 'Number of healthy agents'
      });
      
      // Store system health data
      await redisService.setCache('system:health', {
        ...systemHealth,
        healthScore,
        timestamp: new Date().toISOString()
      }, 300); // Cache for 5 minutes
      
    } catch (error) {
      winston.error('System monitoring failed:', error);
    }
  }

  async collectPerformanceMetrics() {
    for (const [agentId, agent] of this.agents) {
      try {
        if (agent.collectMetrics) {
          const metrics = await agent.collectMetrics();
          
          for (const [metricName, metricData] of Object.entries(metrics)) {
            await this.recordPerformanceMetric(
              agentId, 
              metricName, 
              metricData.value, 
              metricData
            );
          }
        }
      } catch (error) {
        winston.error(`Failed to collect metrics for agent ${agentId}:`, error);
      }
    }
  }

  startHealthMonitoring() {
    // Clear existing interval if any
    if (this.healthCheckInterval) {
      clearInterval(this.healthCheckInterval);
    }
    
    // Run initial health check
    this.checkAgentHealth();
    
    // Set up periodic health monitoring
    this.healthCheckInterval = setInterval(() => {
      this.checkAgentHealth();
    }, 60000); // Every minute
  }

  async shutdown() {
    try {
      winston.info('🛑 Shutting down Agent Manager...');
      
      // Stop health monitoring
      if (this.healthCheckInterval) {
        clearInterval(this.healthCheckInterval);
      }
      
      // Shutdown all agents
      for (const [agentId, agent] of this.agents) {
        try {
          if (agent.stop) {
            await agent.stop();
          }
          await this.setAgentStatus(agentId, 'offline', { reason: 'shutdown' });
        } catch (error) {
          winston.error(`Failed to shutdown agent ${agentId}:`, error);
        }
      }
      
      // Close task queues
      for (const [agentId, queue] of this.taskQueues) {
        try {
          await queue.close();
        } catch (error) {
          winston.error(`Failed to close queue for agent ${agentId}:`, error);
        }
      }
      
      this.isInitialized = false;
      this.emit('shutdown');
      
      winston.info('✅ Agent Manager shutdown complete');
    } catch (error) {
      winston.error('Error during Agent Manager shutdown:', error);
    }
  }
}

module.exports = AgentManager;
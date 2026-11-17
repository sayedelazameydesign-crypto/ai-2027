const express = require('express');
const auth = require('../middleware/auth');
const winston = require('winston');

const router = express.Router();

// Get all agents
router.get('/', auth.authMiddleware, auth.rateLimitByUser(60000, 50), async (req, res) => {
  try {
    const agents = await req.app.locals.agentManager.getAllAgents();
    
    res.json({
      agents,
      total: agents.length,
      online: agents.filter(a => a.status === 'online').length
    });

  } catch (error) {
    winston.error('Get agents error:', error);
    res.status(500).json({
      error: 'Failed to get agents',
      code: 'AGENTS_FETCH_ERROR'
    });
  }
});

// Get specific agent
router.get('/:agentId', auth.authMiddleware, auth.rateLimitByUser(60000, 100), auth.authorizeAgent(), async (req, res) => {
  try {
    const agent = req.agent;
    
    // Get additional agent data
    const [status, metrics, logs] = await Promise.all([
      req.app.locals.redisService.getAgentStatus(agent.id),
      req.app.locals.databaseService.getPerformanceMetrics(agent.id, null, '24h'),
      req.app.locals.databaseService.query(
        'SELECT * FROM agent_logs WHERE agent_id = $1 ORDER BY created_at DESC LIMIT 10',
        [agent.id]
      )
    ]);

    res.json({
      agent,
      status,
      recent_metrics: metrics,
      recent_logs: logs.rows
    });

  } catch (error) {
    winston.error('Get agent error:', error);
    res.status(500).json({
      error: 'Failed to get agent',
      code: 'AGENT_FETCH_ERROR'
    });
  }
});

// Get agent tasks
router.get('/:agentId/tasks', auth.authMiddleware, auth.authorizeAgent(), async (req, res) => {
  try {
    const { status, limit = 50 } = req.query;
    const tasks = await req.app.locals.databaseService.getTasks(agentId, status, parseInt(limit));
    
    res.json({
      tasks,
      total: tasks.length
    });

  } catch (error) {
    winston.error('Get agent tasks error:', error);
    res.status(500).json({
      error: 'Failed to get agent tasks',
      code: 'TASKS_FETCH_ERROR'
    });
  }
});

// Get agent metrics
router.get('/:agentId/metrics', auth.authMiddleware, auth.authorizeAgent(), async (req, res) => {
  try {
    const { timeRange = '24h', metricName } = req.query;
    const metrics = await req.app.locals.databaseService.getPerformanceMetrics(agentId, metricName, timeRange);
    
    res.json({
      metrics,
      timeRange,
      total: metrics.length
    });

  } catch (error) {
    winston.error('Get agent metrics error:', error);
    res.status(500).json({
      error: 'Failed to get agent metrics',
      code: 'METRICS_FETCH_ERROR'
    });
  }
});

// Get agent messages/conversations
router.get('/:agentId/messages', auth.authMiddleware, auth.authorizeAgent(), async (req, res) => {
  try {
    const { limit = 50 } = req.query;
    const messages = await req.app.locals.databaseService.getMessages(agentId, parseInt(limit));
    
    res.json({
      messages,
      total: messages.length
    });

  } catch (error) {
    winston.error('Get agent messages error:', error);
    res.status(500).json({
      error: 'Failed to get agent messages',
      code: 'MESSAGES_FETCH_ERROR'
    });
  }
});

// Assign task to agent
router.post('/:agentId/tasks', auth.authMiddleware, auth.authorizeAgent(), async (req, res) => {
  try {
    const { title, description, task_type, payload, priority = 1, delay = 0 } = req.body;
    
    if (!title || !task_type) {
      return res.status(400).json({
        error: 'Title and task type are required',
        code: 'MISSING_REQUIRED_FIELDS'
      });
    }

    const taskData = {
      type: task_type,
      title,
      description,
      payload: payload || {},
      priority
    };

    const task = await req.app.locals.agentManager.assignTask(agentId, taskData, {
      assignedBy: req.user.id,
      delay
    });

    res.status(201).json({
      message: 'Task assigned successfully',
      task
    });

  } catch (error) {
    winston.error('Assign task error:', error);
    res.status(500).json({
      error: 'Failed to assign task',
      code: 'TASK_ASSIGNMENT_ERROR'
    });
  }
});

// Send message to agent
router.post('/:agentId/messages', auth.authMiddleware, auth.authorizeAgent(), async (req, res) => {
  try {
    const { content, message_type = 'text', metadata = {} } = req.body;
    
    if (!content) {
      return res.status(400).json({
        error: 'Message content is required',
        code: 'MISSING_MESSAGE_CONTENT'
      });
    }

    // Create message in database
    const message = await req.app.locals.databaseService.createMessage(
      agentId,
      'user',
      content,
      message_type,
      { ...metadata, user_id: req.user.id }
    );

    // Emit to connected clients via WebSocket
    req.app.locals.io.emit('agent:new_message', {
      agentId,
      message
    });

    res.status(201).json({
      message: 'Message sent successfully',
      message
    });

  } catch (error) {
    winston.error('Send message error:', error);
    res.status(500).json({
      error: 'Failed to send message',
      code: 'MESSAGE_SEND_ERROR'
    });
  }
});

// Update agent configuration
router.put('/:agentId/config', auth.authMiddleware, auth.authorize(['admin']), async (req, res) => {
  try {
    const { configuration } = req.body;
    
    if (!configuration) {
      return res.status(400).json({
        error: 'Configuration is required',
        code: 'MISSING_CONFIGURATION'
      });
    }

    // Update agent in database
    await req.app.locals.databaseService.query(`
      UPDATE agents 
      SET config = $1, updated_at = NOW()
      WHERE id = $2
    `, [JSON.stringify(configuration), agentId]);

    // Update agent instance
    const agent = req.app.locals.agentManager.getAgent(agentId);
    if (agent && agent.setConfiguration) {
      agent.setConfiguration(configuration);
    }

    res.json({
      message: 'Agent configuration updated successfully'
    });

  } catch (error) {
    winston.error('Update agent config error:', error);
    res.status(500).json({
      error: 'Failed to update agent configuration',
      code: 'CONFIG_UPDATE_ERROR'
    });
  }
});

// Restart agent
router.post('/:agentId/restart', auth.authMiddleware, auth.authorize(['admin']), async (req, res) => {
  try {
    await req.app.locals.agentManager.restartAgent(agentId);
    
    res.json({
      message: 'Agent restart initiated successfully'
    });

  } catch (error) {
    winston.error('Restart agent error:', error);
    res.status(500).json({
      error: 'Failed to restart agent',
      code: 'AGENT_RESTART_ERROR'
    });
  }
});

// Get agent health status
router.get('/:agentId/health', auth.authMiddleware, async (req, res) => {
  try {
    const agent = req.app.locals.agentManager.getAgent(agentId);
    
    if (!agent || !agent.healthCheck) {
      return res.status(404).json({
        error: 'Agent not found or health check not available',
        code: 'HEALTH_CHECK_UNAVAILABLE'
      });
    }

    const health = await agent.healthCheck();
    
    res.json({
      agent_id: agentId,
      health,
      checked_at: new Date().toISOString()
    });

  } catch (error) {
    winston.error('Agent health check error:', error);
    res.status(500).json({
      error: 'Failed to check agent health',
      code: 'HEALTH_CHECK_ERROR'
    });
  }
});

// Get agent capabilities
router.get('/:agentId/capabilities', auth.authMiddleware, async (req, res) => {
  try {
    const agent = req.app.locals.agentManager.getAgent(agentId);
    
    if (!agent || !agent.getCapabilities) {
      return res.status(404).json({
        error: 'Agent not found or capabilities not available',
        code: 'CAPABILITIES_UNAVAILABLE'
      });
    }

    const capabilities = agent.getCapabilities();
    
    res.json({
      agent_id: agentId,
      agent_name: agent.getName(),
      capabilities,
      version: agent.getVersion ? agent.getVersion() : '1.0.0'
    });

  } catch (error) {
    winston.error('Get agent capabilities error:', error);
    res.status(500).json({
      error: 'Failed to get agent capabilities',
      code: 'CAPABILITIES_FETCH_ERROR'
    });
  }
});

module.exports = router;
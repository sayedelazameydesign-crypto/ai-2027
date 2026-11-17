const express = require('express');
const bcrypt = require('bcrypt');
const jwt = require('jsonwebtoken');
const { body, validationResult } = require('express-validator');
const databaseService = require('../services/database');
const redisService = require('../services/redis');
const auth = require('../middleware/auth');
const winston = require('winston');

const router = express.Router();

// User registration
router.post('/register', [
  body('username').isLength({ min: 3, max: 50 }).withMessage('Username must be 3-50 characters'),
  body('email').isEmail().withMessage('Valid email required'),
  body('password').isLength({ min: 8 }).withMessage('Password must be at least 8 characters')
], async (req, res) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      return res.status(400).json({
        error: 'Validation failed',
        details: errors.array()
      });
    }

    const { username, email, password, role = 'user' } = req.body;

    // Check if user already exists
    const existingUser = await databaseService.query(
      'SELECT id FROM users WHERE username = $1 OR email = $2',
      [username, email]
    );

    if (existingUser.rows.length > 0) {
      return res.status(409).json({
        error: 'User already exists',
        code: 'USER_EXISTS'
      });
    }

    // Hash password
    const saltRounds = 12;
    const passwordHash = await bcrypt.hash(password, saltRounds);

    // Create user
    const newUser = await databaseService.query(`
      INSERT INTO users (username, email, password_hash, role)
      VALUES ($1, $2, $3, $4)
      RETURNING id, username, email, role, created_at
    `, [username, email, passwordHash, role]);

    const user = newUser.rows[0];

    // Create session
    const sessionData = {
      userId: user.id,
      username: user.username,
      role: user.role,
      createdAt: new Date().toISOString()
    };

    await redisService.setUserSession(user.id, sessionData, 3600);

    winston.info(`New user registered: ${username}`);

    res.status(201).json({
      message: 'User registered successfully',
      user: {
        id: user.id,
        username: user.username,
        email: user.email,
        role: user.role,
        created_at: user.created_at
      }
    });

  } catch (error) {
    winston.error('Registration error:', error);
    res.status(500).json({
      error: 'Registration failed',
      code: 'REGISTRATION_ERROR'
    });
  }
});

// User login
router.post('/login', [
  body('username').notEmpty().withMessage('Username is required'),
  body('password').notEmpty().withMessage('Password is required')
], async (req, res) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      return res.status(400).json({
        error: 'Validation failed',
        details: errors.array()
      });
    }

    const { username, password, remember_me = false } = req.body;
    const clientIP = req.ip || req.connection.remoteAddress;

    // Get user from database
    const userResult = await databaseService.query(`
      SELECT id, username, email, password_hash, role, is_active
      FROM users 
      WHERE username = $1 OR email = $1
    `, [username]);

    if (userResult.rows.length === 0) {
      return res.status(401).json({
        error: 'Invalid credentials',
        code: 'INVALID_CREDENTIALS'
      });
    }

    const user = userResult.rows[0];

    if (!user.is_active) {
      return res.status(401).json({
        error: 'Account is deactivated',
        code: 'ACCOUNT_DEACTIVATED'
      });
    }

    // Check password
    const isValidPassword = await bcrypt.compare(password, user.password_hash);
    if (!isValidPassword) {
      return res.status(401).json({
        error: 'Invalid credentials',
        code: 'INVALID_CREDENTIALS'
      });
    }

    // Update last login
    await databaseService.query(
      'UPDATE users SET last_login = NOW() WHERE id = $1',
      [user.id]
    );

    // Generate JWT token
    const tokenPayload = {
      userId: user.id,
      username: user.username,
      role: user.role
    };

    const token = jwt.sign(tokenPayload, process.env.JWT_SECRET, {
      expiresIn: remember_me ? '30d' : '24h'
    });

    // Create session
    const sessionData = {
      token,
      userId: user.id,
      username: user.username,
      role: user.role,
      ip: clientIP,
      userAgent: req.get('User-Agent'),
      createdAt: new Date().toISOString(),
      lastActivity: new Date().toISOString()
    };

    await redisService.setUserSession(user.id, sessionData, remember_me ? 2592000 : 3600);

    // Store session in database
    await databaseService.query(`
      INSERT INTO user_sessions (user_id, token_hash, expires_at, ip_address, user_agent)
      VALUES ($1, $2, $3, $4, $5)
    `, [
      user.id,
      await bcrypt.hash(token, 10),
      new Date(Date.now() + (remember_me ? 2592000000 : 86400000)),
      clientIP,
      req.get('User-Agent')
    ]);

    winston.info(`User logged in: ${user.username}`);

    res.json({
      message: 'Login successful',
      token,
      user: {
        id: user.id,
        username: user.username,
        email: user.email,
        role: user.role
      },
      expires_in: remember_me ? 2592000 : 86400
    });

  } catch (error) {
    winston.error('Login error:', error);
    res.status(500).json({
      error: 'Login failed',
      code: 'LOGIN_ERROR'
    });
  }
});

// User logout
router.post('/logout', auth.authMiddleware, async (req, res) => {
  try {
    const userId = req.user.id;

    // Remove session from Redis
    await redisService.removeUserSession(userId);

    // Remove session from database
    await databaseService.query(
      'DELETE FROM user_sessions WHERE user_id = $1',
      [userId]
    );

    winston.info(`User logged out: ${req.user.username}`);

    res.json({
      message: 'Logout successful'
    });

  } catch (error) {
    winston.error('Logout error:', error);
    res.status(500).json({
      error: 'Logout failed',
      code: 'LOGOUT_ERROR'
    });
  }
});

// Refresh token
router.post('/refresh', auth.authMiddleware, async (req, res) => {
  try {
    const user = req.user;

    // Generate new token
    const newToken = jwt.sign({
      userId: user.id,
      username: user.username,
      role: user.role
    }, process.env.JWT_SECRET, {
      expiresIn: '24h'
    });

    // Update session
    const sessionData = await redisService.getUserSession(user.id);
    if (sessionData) {
      sessionData.token = newToken;
      await redisService.setUserSession(user.id, sessionData, 3600);
    }

    res.json({
      message: 'Token refreshed successfully',
      token: newToken,
      expires_in: 86400
    });

  } catch (error) {
    winston.error('Token refresh error:', error);
    res.status(500).json({
      error: 'Token refresh failed',
      code: 'REFRESH_ERROR'
    });
  }
});

// Get current user profile
router.get('/profile', auth.authMiddleware, async (req, res) => {
  try {
    const userResult = await databaseService.query(`
      SELECT id, username, email, role, created_at, last_login
      FROM users 
      WHERE id = $1
    `, [req.user.id]);

    if (userResult.rows.length === 0) {
      return res.status(404).json({
        error: 'User not found',
        code: 'USER_NOT_FOUND'
      });
    }

    const user = userResult.rows[0];

    res.json({
      user: {
        id: user.id,
        username: user.username,
        email: user.email,
        role: user.role,
        created_at: user.created_at,
        last_login: user.last_login
      }
    });

  } catch (error) {
    winston.error('Get profile error:', error);
    res.status(500).json({
      error: 'Failed to get profile',
      code: 'PROFILE_ERROR'
    });
  }
});

// Update user profile
router.put('/profile', auth.authMiddleware, [
  body('email').optional().isEmail().withMessage('Valid email required'),
  body('username').optional().isLength({ min: 3, max: 50 }).withMessage('Username must be 3-50 characters')
], async (req, res) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      return res.status(400).json({
        error: 'Validation failed',
        details: errors.array()
      });
    }

    const { email, username } = req.body;
    const userId = req.user.id;

    const updates = [];
    const values = [];
    let paramIndex = 1;

    if (email) {
      updates.push(`email = $${paramIndex}`);
      values.push(email);
      paramIndex++;
    }

    if (username) {
      updates.push(`username = $${paramIndex}`);
      values.push(username);
      paramIndex++;
    }

    if (updates.length === 0) {
      return res.status(400).json({
        error: 'No updates provided',
        code: 'NO_UPDATES'
      });
    }

    values.push(userId);

    const updatedUser = await databaseService.query(`
      UPDATE users 
      SET ${updates.join(', ')}, updated_at = NOW()
      WHERE id = $${paramIndex}
      RETURNING id, username, email, role, updated_at
    `, values);

    const user = updatedUser.rows[0];

    // Update session
    const sessionData = await redisService.getUserSession(userId);
    if (sessionData) {
      sessionData.username = user.username;
      await redisService.setUserSession(userId, sessionData, 3600);
    }

    res.json({
      message: 'Profile updated successfully',
      user: {
        id: user.id,
        username: user.username,
        email: user.email,
        role: user.role,
        updated_at: user.updated_at
      }
    });

  } catch (error) {
    winston.error('Update profile error:', error);
    res.status(500).json({
      error: 'Failed to update profile',
      code: 'UPDATE_ERROR'
    });
  }
});

// Change password
router.put('/change-password', auth.authMiddleware, [
  body('current_password').notEmpty().withMessage('Current password is required'),
  body('new_password').isLength({ min: 8 }).withMessage('New password must be at least 8 characters')
], async (req, res) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      return res.status(400).json({
        error: 'Validation failed',
        details: errors.array()
      });
    }

    const { current_password, new_password } = req.body;
    const userId = req.user.id;

    // Get current password hash
    const userResult = await databaseService.query(
      'SELECT password_hash FROM users WHERE id = $1',
      [userId]
    );

    const user = userResult.rows[0];

    // Verify current password
    const isValidPassword = await bcrypt.compare(current_password, user.password_hash);
    if (!isValidPassword) {
      return res.status(401).json({
        error: 'Current password is incorrect',
        code: 'INVALID_PASSWORD'
      });
    }

    // Hash new password
    const saltRounds = 12;
    const newPasswordHash = await bcrypt.hash(new_password, saltRounds);

    // Update password
    await databaseService.query(
      'UPDATE users SET password_hash = $1, updated_at = NOW() WHERE id = $2',
      [newPasswordHash, userId]
    );

    // Invalidate all sessions
    await redisService.removeUserSession(userId);
    await databaseService.query(
      'DELETE FROM user_sessions WHERE user_id = $1',
      [userId]
    );

    res.json({
      message: 'Password changed successfully'
    });

  } catch (error) {
    winston.error('Change password error:', error);
    res.status(500).json({
      error: 'Failed to change password',
      code: 'PASSWORD_CHANGE_ERROR'
    });
  }
});

// Check authentication status
router.get('/status', auth.authMiddleware, async (req, res) => {
  try {
    const sessionData = await redisService.getUserSession(req.user.id);
    
    res.json({
      authenticated: true,
      user: req.user,
      session: {
        valid: !!sessionData,
        expires_at: sessionData?.expires_at,
        last_activity: sessionData?.lastActivity
      }
    });

  } catch (error) {
    winston.error('Auth status error:', error);
    res.status(500).json({
      error: 'Failed to get auth status',
      code: 'STATUS_ERROR'
    });
  }
});

// Admin-only: Create user
router.post('/create-user', auth.authMiddleware, auth.authorize(['admin']), [
  body('username').isLength({ min: 3, max: 50 }),
  body('email').isEmail(),
  body('password').isLength({ min: 8 }),
  body('role').isIn(['admin', 'user', 'viewer']).withMessage('Invalid role')
], async (req, res) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      return res.status(400).json({
        error: 'Validation failed',
        details: errors.array()
      });
    }

    const { username, email, password, role } = req.body;

    // Check if user already exists
    const existingUser = await databaseService.query(
      'SELECT id FROM users WHERE username = $1 OR email = $2',
      [username, email]
    );

    if (existingUser.rows.length > 0) {
      return res.status(409).json({
        error: 'User already exists',
        code: 'USER_EXISTS'
      });
    }

    // Hash password
    const saltRounds = 12;
    const passwordHash = await bcrypt.hash(password, saltRounds);

    // Create user
    const newUser = await databaseService.query(`
      INSERT INTO users (username, email, password_hash, role)
      VALUES ($1, $2, $3, $4)
      RETURNING id, username, email, role, created_at
    `, [username, email, passwordHash, role]);

    const user = newUser.rows[0];

    res.status(201).json({
      message: 'User created successfully',
      user
    });

  } catch (error) {
    winston.error('Create user error:', error);
    res.status(500).json({
      error: 'Failed to create user',
      code: 'CREATE_USER_ERROR'
    });
  }
});

// Health check for auth service
router.get('/health', (req, res) => {
  res.json({
    status: 'healthy',
    service: 'auth',
    timestamp: new Date().toISOString(),
    database: 'connected',
    redis: 'connected'
  });
});

module.exports = router;
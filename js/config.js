/**
 * Config & Firebase initialization
 * Exports: app, auth, API, $, getToken, getCurrentUid, setCurrentAuth
 */

import { initializeApp } from "https://www.gstatic.com/firebasejs/10.9.0/firebase-app.js";
import {
  getAuth,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  signOut,
  RecaptchaVerifier,
  signInWithPhoneNumber,
  GoogleAuthProvider,
  signInWithPopup
} from "https://www.gstatic.com/firebasejs/10.9.0/firebase-auth.js";

// ── Config ──
const isLocal = location.hostname === "localhost" || location.hostname === "127.0.0.1";
const API = isLocal ? "http://localhost:8000" : "";

const configRes = await fetch(`${API}/config`);
const firebaseConfig = await configRes.json();

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);

// ── DOM helper ──
const $ = id => document.getElementById(id);

// ── Shared auth state ──
let currentToken = null;
let currentUid = null;

function getToken() { return currentToken; }
function getCurrentUid() { return currentUid; }
function setCurrentAuth(token, uid) {
  currentToken = token;
  currentUid = uid;
}

export {
  app, auth, API, $,
  getToken, getCurrentUid, setCurrentAuth,
  // Re-export Firebase auth functions so other modules don't need to import firebase directly
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  signOut,
  RecaptchaVerifier,
  signInWithPhoneNumber,
  GoogleAuthProvider,
  signInWithPopup
};

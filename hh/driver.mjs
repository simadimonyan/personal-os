#!/usr/bin/env node
/**
 * HH.ru skill driver
 * Usage: node driver.mjs <tool_name> [json_args]
 * Output: JSON to stdout, errors to stderr
 */

import {
  checkAuth, login, saveSession,
  searchVacancies, getVacancyDetails,
  getResumes, getResumeText,
  applyToVacancy, getApplications,
  openPageManually, getPageHtml,
} from './hh-client.js';
import { closeBrowser } from './browser.js';

const [,, tool, argsJson] = process.argv;
const args = argsJson ? JSON.parse(argsJson) : {};

async function run() {
  try {
    let result;
    switch (tool) {
      case 'hh_check_auth':
        result = await checkAuth();
        break;
      case 'hh_login':
        result = await login(args.login, args.password);
        break;
      case 'hh_save_session':
        result = await saveSession();
        break;
      case 'hh_search_vacancies':
        result = await searchVacancies(args);
        break;
      case 'hh_get_vacancy':
        result = await getVacancyDetails(args.vacancy_id);
        break;
      case 'hh_get_resumes':
        result = await getResumes();
        break;
      case 'hh_get_resume_text':
        result = await getResumeText(args.resume_id);
        break;
      case 'hh_apply':
        result = await applyToVacancy(args.vacancy_id, args.resume_id, args.cover_letter);
        break;
      case 'hh_get_applications':
        result = await getApplications();
        break;
      case 'hh_open_page':
        result = await openPageManually(args.url);
        break;
      case 'hh_get_page_html':
        result = await getPageHtml(args.selector);
        break;
      default:
        throw new Error(`Unknown tool: ${tool}. Available: hh_check_auth, hh_login, hh_save_session, hh_search_vacancies, hh_get_vacancy, hh_get_resumes, hh_get_resume_text, hh_apply, hh_get_applications, hh_open_page, hh_get_page_html`);
    }
    console.log(JSON.stringify(result, null, 2));
  } catch (err) {
    console.error(JSON.stringify({ error: err.message }));
    process.exit(1);
  } finally {
    await closeBrowser().catch(() => {});
    process.exit(0);
  }
}

if (!tool) {
  console.error('Usage: node driver.mjs <tool_name> [json_args]\nExample: node driver.mjs hh_search_vacancies \'{"query":"Python","area":"1"}\'');
  process.exit(1);
}

run();

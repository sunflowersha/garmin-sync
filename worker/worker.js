export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
};

async function dispatch(env, attempt = 1) {
  const res = await fetch(
    'https://api.github.com/repos/sunflowersha/garmin-sync/actions/workflows/notify.yml/dispatches',
    {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.GH_TOKEN}`,
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'comrades-notify-trigger',
        'X-GitHub-Api-Version': '2022-11-28',
      },
      body: JSON.stringify({ ref: 'master', inputs: {} }),
    }
  );
  if (res.status !== 204) {
    const body = await res.text();
    console.error(`dispatch attempt ${attempt} failed: ${res.status} ${body}`);
    if (attempt < 2) return dispatch(env, attempt + 1);
    throw new Error(`workflow dispatch failed after 2 attempts: ${res.status}`);
  }
  console.log('notify.yml dispatched');
}

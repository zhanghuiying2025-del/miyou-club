import { api } from '../../shared/api.js';

let session;
export async function customerApi(path, options) {
  session ??= api('/customer/session').catch(error => {
    session = undefined;
    throw error;
  });
  await session;
  return api(path, options);
}

export const activityPath = id => '/customer/activities/' + encodeURIComponent(id);
export const workspaceUrl = id => '/workspace/' + encodeURIComponent(id);
export const planUrl = (id, plan) => '/activities/' + encodeURIComponent(id) + '/plans/' + encodeURIComponent(plan);

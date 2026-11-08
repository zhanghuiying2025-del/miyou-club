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
export const weatherPreviewPath = ({ city = '', destination = '', date = '', endDate = '' }) =>
  '/customer/weather?city=' + encodeURIComponent(city.trim()) +
  '&destination=' + encodeURIComponent(destination.trim()) +
  '&start_date=' + encodeURIComponent(date) +
  '&end_date=' + encodeURIComponent(endDate || date);
export const activityWeatherPath = id => '/customer/activities/' + encodeURIComponent(id) + '/weather';
